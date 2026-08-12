"""Declarative device → entity registry.

This is the single place that describes which Home Assistant entities each appliance
category exposes. Platform modules are generic: they read the :class:`CategorySpec` for a
device's ``device_type`` and build entities from these descriptors, skipping any whose
``capability`` gate is not satisfied by the model's cloud-reported capability profile.

Adding a category = adding one ``CategorySpec`` here (plus, for a native platform such as
``climate``/``water_heater``/``light``, a small handler in that platform module).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    EntityCategory,
    Platform,
    UnitOfTemperature,
    UnitOfTime,
)

from .conditions import (
    NO_GATES,
    Block,
    Cond,
    Gates,
    Is,
    StateIs,
    StateRule,
)

# --- Value transforms referenced by SensorSpec.transform ---------------------------------
# Applied by the sensor platform. Kept as string tags so specs stay declarative/importable.
TRANSFORM_REMAIN_MINUTES = "remain_minutes"  # remainTimeH*256 + remainTimeL
TRANSFORM_SECONDS_MINUTES = "seconds_minutes"  # whole seconds -> minutes (rounded up)
TRANSFORM_OVEN_STATUS = "oven_status"  # devstatus + preheat phase + reachability

#: Categories that meter their own water and electricity. Creating consumption sensors
#: for a lamp would produce readings that are permanently unknown.
METERED_CATEGORIES = frozenset({"dishwasher", "washer"})

# Blanking one of these would read as a counter reset to long-term statistics.
_LIFETIME_TOTALS = (SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING)


def _faulted(key: str, codes: Mapping[str, str]) -> Cond:
    """``key`` reports one of the known fault codes.

    Spelled as an explicit list rather than "not zero": the field is present on every frame
    and an unmapped value would otherwise put the whole appliance into a fault state it is
    not in. Unknown codes are still caught, by the ``problem`` binary sensor.
    """
    return Is(key, *(code for code in codes if code != "0"))


@dataclass(frozen=True)
class SensorSpec:
    key: str
    translation_key: str
    device_class: SensorDeviceClass | None = None
    unit: str | None = None
    state_class: SensorStateClass | None = None
    value_map: Mapping[str, str] | None = None
    transform: str | None = None
    # Explicit enum options; needed when the labels come from a transform rather than a map.
    options: tuple[str, ...] | None = None
    capability: str | None = None
    entity_category: EntityCategory | None = None
    entity_registry_enabled_default: bool = True
    gates: Gates = NO_GATES

    def __post_init__(self) -> None:
        if self.gates.blocks:
            raise ValueError(f"sensor {self.key}: read-only, a write can never be refused")
        if self.state_class in _LIFETIME_TOTALS and self.gates.meaningful_when is not None:
            # Long-term statistics treat a gap as a counter reset and a reset as a spike.
            raise ValueError(
                f"sensor {self.key}: a lifetime total must never be blanked by a gate"
            )


@dataclass(frozen=True)
class BinarySensorSpec:
    key: str
    translation_key: str
    device_class: BinarySensorDeviceClass | None = None
    on_values: tuple[str, ...] = ("1",)
    # ``True`` flips the test to "on when the value is NOT one of ``on_values``".
    invert: bool = False
    capability: str | None = None
    # Fallback for models that report one packed status integer instead of discrete keys.
    summary_key: str | None = None
    summary_bit: int | None = None
    # Distinct unique-id suffix; required when two sensors read the same status key.
    uid: str | None = None
    entity_category: EntityCategory | None = None
    entity_registry_enabled_default: bool = True
    gates: Gates = NO_GATES

    def __post_init__(self) -> None:
        if self.gates.blocks:
            raise ValueError(f"binary sensor {self.key}: read-only, nothing to refuse")


@dataclass(frozen=True)
class SwitchSpec:
    key: str
    translation_key: str
    on_command: Mapping[str, str] = field(default_factory=dict)
    off_command: Mapping[str, str] = field(default_factory=dict)
    on_values: tuple[str, ...] = ("1",)
    capability: str | None = None
    entity_category: EntityCategory | None = None
    gates: Gates = NO_GATES


@dataclass(frozen=True)
class SelectSpec:
    key: str
    translation_key: str
    command_key: str
    # Static label -> cloud-code map, OR pull options from the capability profile.
    options: Mapping[str, str] = field(default_factory=dict)
    options_from_capability: str | None = None
    extra_command: Mapping[str, str] = field(default_factory=dict)
    capability: str | None = None
    entity_category: EntityCategory | None = None
    gates: Gates = NO_GATES


@dataclass(frozen=True)
class NumberSpec:
    key: str
    translation_key: str
    command_key: str
    native_min: int = 0
    native_max: int = 100
    step: int = 1
    # If set, the max is taken from this capability gear (e.g. rinse_aid_gear).
    max_from_gear: str | None = None
    capability: str | None = None
    entity_category: EntityCategory | None = None
    gates: Gates = NO_GATES


@dataclass(frozen=True)
class ButtonSpec:
    key: str
    translation_key: str
    command: Mapping[str, str] = field(default_factory=dict)
    capability: str | None = None
    entity_category: EntityCategory | None = None
    gates: Gates = NO_GATES

    def __post_init__(self) -> None:
        if self.gates.meaningful_when is not None:
            raise ValueError(f"button {self.key}: has no value to blank")


@dataclass(frozen=True)
class LightConfig:
    """Native `light` mapping. Brightness and color temperature use a 0-255 device scale."""

    power_key: str = "power"
    power_on: str = "1"
    power_off: str = "0"
    brightness_key: str | None = "bright"
    color_temp_key: str | None = "colorTemp"
    warm_kelvin: int = 2700
    cool_kelvin: int = 6500
    effect_key: str | None = "mode"
    effects: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WaterHeaterConfig:
    """Native `water_heater` mapping. Operation modes are flag combinations."""

    power_key: str = "power"
    current_temp_keys: tuple[str, ...] = ("cur_temperature", "temp")
    target_temp_key: str = "temp"
    min_temp: int = 35
    max_temp: int = 75
    operations: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    # (key, expected value, mode). Checked in order, first match wins, else the default.
    operation_flags: tuple[tuple[str, str, str], ...] = ()
    default_operation: str = "normal"
    # (key, value) at which the appliance itself refuses manual temperature entry — the
    # 51020ED8 does this while in its "Smart" mode, which picks its own setpoint.
    temp_locked_when: tuple[str, str] | None = None


@dataclass(frozen=True)
class ClimateConfig:
    """Native `climate` mapping for air conditioners."""

    power_key: str = "power"
    mode_key: str = "mode"
    # cloud mode code -> HVACMode value (used only when powered on)
    hvac_mode_codes: Mapping[str, str] = field(default_factory=dict)
    target_temp_key: str = "temp"
    current_temp_key: str | None = "indoorTemp"
    min_temp: int = 16
    max_temp: int = 30
    fan_key: str = "windSpeed"
    fan_modes: Mapping[str, str] = field(default_factory=dict)  # label -> code


@dataclass(frozen=True)
class OvenProgram:
    """One cooking programme plus the limits the appliance enforces for it.

    Ranges are programme-specific: the appliance rejects a setpoint outside them, and some
    programmes take no temperature (time-only) or no pre-heat at all.
    """

    code: str
    min_temp: int = 50
    max_temp: int = 250
    temp_step: int = 5
    default_temp: int = 180
    supports_temp: bool = True
    supports_preheat: bool = True
    max_minutes: int = 300


@dataclass(frozen=True)
class OvenConfig:
    """Composite oven mapping.

    Home Assistant has no oven platform, so an oven is modelled as a programme composer:
    the mode/temperature/duration/probe/pre-heat controls stage their values locally and the
    *start* button submits them as the one cook instruction the cloud API accepts.
    """

    programs: Mapping[str, OvenProgram]
    mode_key: str = "cookMode"
    temp_key: str = "upperTemp"
    lower_temp_key: str = "lowerTemp"
    time_key: str = "cookTime"
    probe_key: str = "probeTemp"
    preheat_key: str = "preHeat"
    default_minutes: int = 30
    probe_min_temp: int = 30
    probe_max_temp: int = 100
    probe_default_temp: int = 75
    # Fixed step parameters carried by every cook instruction.
    fire: str = "255"
    size: str = "1"
    next_action: str = "1"


@dataclass(frozen=True)
class DishwasherConfig:
    """Composite dishwasher mapping for starting a cycle.

    The appliance accepts a wash only as one whole instruction — programme, extra option,
    wash zone and the run state together. So the controls stage their values locally and the
    *start* button submits them, the same way the oven composes a cook.
    """

    programs: Mapping[str, str]
    # Extra option label -> (cloud code, capability that must be advertised).
    extras: Mapping[str, tuple[str, str]]
    zones: Mapping[str, str]
    mode_key: str = "modeEU"
    extra_key: str = "addFuncEU"
    zone_key: str = "washArea"
    power_key: str = "power"
    # Value of ``power`` that means "run the composed programme now".
    start_value: str = "3"
    zone_capability: str = "alternate_wash"
    default_zone: str = "3"
    no_extra_code: str = "0"


@dataclass(frozen=True)
class SnapshotTrigger:
    """When a full status snapshot has to be fetched again.

    The push channel does not carry everything the appliance knows: its frames are a
    subset that leaves out the lifetime counters (water used, energy used, cycles run).
    Those live only in the HTTP snapshot, which the push-first design deliberately stops
    asking for — so left alone the counters would freeze at whatever the first snapshot
    said, or stay unknown if it predated them.

    They also only ever move when a cycle ends. Re-fetching exactly then keeps them
    honest for the cost of one request per wash, instead of the steady polling that would
    put Home Assistant back in competition with the mobile app for the account session.
    """

    key: str
    settled_values: frozenset[str]

    def fires(self, before: object, after: object) -> bool:
        """True when ``key`` has just come to rest, i.e. a cycle finished."""
        if before is None or after is None:
            return False
        return str(after) in self.settled_values and str(before) not in self.settled_values


@dataclass(frozen=True)
class CategorySpec:
    device_type: str
    category: str
    primary_platform: Platform | None = None
    light: LightConfig | None = None
    water_heater: WaterHeaterConfig | None = None
    climate: ClimateConfig | None = None
    oven: OvenConfig | None = None
    dishwasher: DishwasherConfig | None = None
    sensors: tuple[SensorSpec, ...] = ()
    binary_sensors: tuple[BinarySensorSpec, ...] = ()
    switches: tuple[SwitchSpec, ...] = ()
    selects: tuple[SelectSpec, ...] = ()
    numbers: tuple[NumberSpec, ...] = ()
    buttons: tuple[ButtonSpec, ...] = ()
    snapshot_after: SnapshotTrigger | None = None
    # The appliance's own state machine, in the one place it is written down. Rules are
    # ordered and the first match wins, exactly as the vendor's own resolver works.
    states: tuple[StateRule, ...] = ()
    # Ordered write policy shared by every control of the category. Each vendor plugin has
    # exactly one such guard; a control opts out of individual reasons via ``Gates.exempt``.
    guard: tuple[Block, ...] = ()


# =========================================================================================
# 0xE1 — Dishwasher (verified ground truth; the first fully-modelled category).
# =========================================================================================

_WASHING_STAGE = {
    "0": "idle",
    "1": "pre_wash",
    "2": "main_wash",
    "3": "rinse",
    "4": "drying",
    "5": "finished",
}
# Translation keys must match [a-z0-9-_]+, so fault labels are lowercase; the display text
# in strings.json still shows the panel code the appliance itself reports (E1, EC, ...).
_FAULT_CODE = {
    "0": "none", "1": "e1", "2": "e2", "3": "e3", "4": "e4",
    "8": "e8", "12": "ec", "14": "eh", "16": "c4", "17": "l4",
}
_PROGRAM = {
    "1": "auto", "2": "intensive", "3": "universal", "4": "eco", "5": "delicate",
    "6": "90mins", "7": "rapid", "8": "soak", "12": "party", "15": "hygiene",
    "16": "self_cleaning",
}

DISHWASHER_PROGRAMS: dict[str, str] = {
    "auto": "1", "intensive": "2", "universal": "3", "eco": "4", "delicate": "5",
    "90mins": "6", "rapid": "7", "soak": "8", "party": "12", "hygiene": "15",
    "self_cleaning": "16",
}
# Extra option -> (cloud code, capability the model must advertise). Only one at a time:
# the appliance takes a single value here, not a set of flags.
DISHWASHER_EXTRAS: dict[str, tuple[str, str]] = {
    "extra_drying": ("1", "extra_drying"),
    "half_load": ("2", "half"),
    "power_wash": ("4", "power_wash"),
    "turbo_speed": ("6", "turbo_speed"),
}
DISHWASHER_ZONES: dict[str, str] = {"upper": "1", "lower": "2", "both": "3"}

# The appliance's state machine, in the order its own resolver evaluates it:
# power decides first and overrides everything, a fault outranks any running state, and
# only then do the run keys get a say. ``power`` is not a boolean here — it is the state
# itself (0 off, 1/5 standby, 2 reserved, 3 working).
DISHWASHER_STATES = (
    StateRule("power_off", Is("power", "0")),
    StateRule("fault", _faulted("faultCode", _FAULT_CODE)),
    StateRule("standby", Is("power", "1", "5")),
    StateRule("delay", Is("power", "2")),
    StateRule("finished", Is("power", "3") & Is("washingState", "5")),
    StateRule("running", Is("runState", "1")),
    StateRule("pause", Is("runState", "2")),
)

# The live countdown and the water temperature only exist while the drum is working.
_DISHWASHER_RUNNING = StateIs("running", "pause")
# The stage additionally survives into "finished", which is a stage the user waits for —
# it is the appliance saying the wash is done, not a leftover from it.
_DISHWASHER_CYCLE = StateIs("running", "pause", "finished")

# The appliance refuses every command while any of these holds, and the order decides
# which reason the user is told. Controls opt out individually — power must stay usable
# exactly when the appliance is off, faulted or open.
DISHWASHER_GUARD = (
    Block(StateIs("power_off"), "appliance_off"),
    Block(StateIs("fault"), "appliance_fault"),
    Block(Is("doorstatus", "0"), "door_open"),
    Block(Is("childLock", "1"), "child_lock"),
)
_POWER_EXEMPT = frozenset({"appliance_off", "appliance_fault", "door_open", "child_lock"})

DISHWASHER = CategorySpec(
    device_type="0xE1",
    category="dishwasher",
    primary_platform=None,  # no native HA platform — composite
    states=DISHWASHER_STATES,
    guard=DISHWASHER_GUARD,
    dishwasher=DishwasherConfig(
        programs=DISHWASHER_PROGRAMS,
        extras=DISHWASHER_EXTRAS,
        zones=DISHWASHER_ZONES,
    ),
    # "finished" and "idle" are the two states a wash settles into, and the lifetime
    # counters are written at that moment — see SnapshotTrigger.
    snapshot_after=SnapshotTrigger("washingState", frozenset({"0", "5"})),
    sensors=(
        # Outside a cycle the appliance keeps reporting the last wash's stage; the vendor
        # app does not render the stage block at all in those states.
        SensorSpec("washingState", "wash_stage", device_class=SensorDeviceClass.ENUM,
                   value_map=_WASHING_STAGE,
                   gates=Gates(meaningful_when=_DISHWASHER_CYCLE)),
        # A programme survives into standby as the *next* wash's choice, so it still means
        # something there — but on a switched-off appliance it is last cycle's leftover.
        SensorSpec("modeEU", "program", device_class=SensorDeviceClass.ENUM,
                   value_map=_PROGRAM,
                   gates=Gates(meaningful_when=~StateIs("power_off"))),
        SensorSpec("faultCode", "fault", device_class=SensorDeviceClass.ENUM,
                   value_map=_FAULT_CODE, entity_category=EntityCategory.DIAGNOSTIC),
        # The live countdown is only live during a wash. Elsewhere the appliance leaves the
        # previous cycle's value standing, which is why a switched-off machine was showing
        # hours "remaining"; the vendor app substitutes a table estimate instead.
        SensorSpec("remainTimeL", "remaining_time", device_class=SensorDeviceClass.DURATION,
                   unit=UnitOfTime.MINUTES, transform=TRANSFORM_REMAIN_MINUTES,
                   gates=Gates(meaningful_when=_DISHWASHER_RUNNING)),
        SensorSpec("realTemp", "temperature", device_class=SensorDeviceClass.TEMPERATURE,
                   unit=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
                   gates=Gates(meaningful_when=_DISHWASHER_RUNNING)),
        SensorSpec("saltTimes", "salt_refills", state_class=SensorStateClass.TOTAL_INCREASING,
                   capability="salt", entity_category=EntityCategory.DIAGNOSTIC,
                   entity_registry_enabled_default=False),
        SensorSpec("brightenAgentTimes", "rinse_aid_refills",
                   state_class=SensorStateClass.TOTAL_INCREASING, capability="rinse_aid",
                   entity_category=EntityCategory.DIAGNOSTIC,
                   entity_registry_enabled_default=False),
    ),
    binary_sensors=(
        BinarySensorSpec("doorstatus", "door", device_class=BinarySensorDeviceClass.DOOR,
                         on_values=("0",)),  # 0 = open
        BinarySensorSpec("salt", "salt_low", device_class=BinarySensorDeviceClass.PROBLEM,
                         on_values=("1",), capability="salt"),
        BinarySensorSpec("brightenAgent", "rinse_aid_low",
                         device_class=BinarySensorDeviceClass.PROBLEM, on_values=("1",),
                         capability="rinse_aid"),
    ),
    switches=(
        # Power is the one control that ignores the whole guard: it has to stay usable
        # exactly when the appliance is off, faulted, open or locked. "2" (reserved) counts
        # as on — a delayed start is a switched-on appliance waiting for its hour.
        SwitchSpec("power", "power", on_command={"power": "1"},
                   off_command={"power": "0"}, on_values=("1", "2", "3", "5"),
                   gates=Gates(exempt=_POWER_EXEMPT)),
        SwitchSpec("runState", "running", on_command={"runState": "1"},
                   off_command={"runState": "2"}, on_values=("1",),
                   gates=Gates(meaningful_when=_DISHWASHER_RUNNING)),
        SwitchSpec("autoDoorOpen", "auto_door_open", on_command={"autoDoorOpen": "1"},
                   off_command={"autoDoorOpen": "2"}, on_values=("1",), capability="auto_open",
                   entity_category=EntityCategory.CONFIG),
    ),
    numbers=(
        NumberSpec("distributorGear", "rinse_aid_level", command_key="distributorGear",
                   native_min=1, native_max=5, max_from_gear="rinse_aid", capability="rinse_aid",
                   entity_category=EntityCategory.CONFIG),
        NumberSpec("softWaterGear", "water_softener", command_key="softWaterGear",
                   native_min=1, native_max=6, max_from_gear="salt", capability="salt",
                   entity_category=EntityCategory.CONFIG),
    ),
)


# =========================================================================================
# 0x13 — Ceiling lamp → native `light` platform (tunable white + scenes).
# =========================================================================================

# A lamp has no cycle: it is on or it is off, and the only reason to write the machine down
# is so the rest of the integration can ask one question of every appliance.
LAMP_STATES = (
    StateRule("off", Is("power", "0")),
    StateRule("on", Is("power", "1")),
)

LAMP = CategorySpec(
    states=LAMP_STATES,
    device_type="0x13",
    category="light",
    primary_platform=Platform.LIGHT,
    light=LightConfig(
        effects={
            "manual": "1", "life": "2", "read": "3", "soft": "4", "cinema": "5", "night": "6",
        },
    ),
)


# =========================================================================================
# 0xE2 — Water heater → native `water_heater` platform.
# Note: modelled from the cloud protocol; verified on one unit (Terma AquaPro WiFi,
# model 51020ED8) — see docs/hcl.md. The rest of the family is still unconfirmed.
# =========================================================================================

# Power says whether the appliance is alive; heatStatus says what the element is doing.
# The vendor screen shows "Off" instead of the heating status whenever power is 0.
WATER_HEATER_STATES = (
    StateRule("power_off", Is("power", "0")),
    StateRule("heating", Is("heatStatus", "1")),
    StateRule("keep_warm", Is("heatStatus", "2")),
    StateRule("standby", Is("heatStatus", "0")),
)

WATER_HEATER = CategorySpec(
    states=WATER_HEATER_STATES,
    device_type="0xE2",
    category="water_heater",
    primary_platform=Platform.WATER_HEATER,
    water_heater=WaterHeaterConfig(
        # `temp` is the setpoint and `targetTemp` is the measured tank temperature — the
        # names are swapped from what they suggest. Confirmed by changing the setpoint on a
        # real unit and observing which field moved: `temp` tracked the new value,
        # `targetTemp` stayed at the (lower) reading the panel showed as current. The vendor
        # app reads both through the same code path for the whole category, not per model,
        # so `targetTemp` is added ahead of the `temp` fallback rather than replacing it —
        # models that never report it keep falling back to `temp` exactly as before.
        current_temp_keys=("cur_temperature", "targetTemp", "temp"),
        # The app's own "Model" screen is a 3-way exclusive picker — Smart / Single (tank) /
        # Double (tank) — not independent eco/smart/high-temp flags. Confirmed on real
        # hardware by cycling all three from the app and capturing the raw payload each
        # time: `cloudSmart` and `bodyNum` move together, never both meaningfully set.
        # `eco` was tried from Home Assistant and never came back in a single query
        # response, and the app has no Eco control anywhere, so it is dropped rather than
        # offered as a mode nothing here confirms this unit acts on. `highTemp` looks tied
        # to the app's separate scheduled disinfect cycle, not this picker — also dropped
        # until that is understood on its own terms. See docs/hcl.md for the raw evidence.
        operations={
            "single": {"cloudSmart": "0", "bodyNum": "1"},
            "double": {"cloudSmart": "0", "bodyNum": "2"},
            "smart": {"cloudSmart": "1"},
        },
        operation_flags=(
            ("cloudSmart", "1", "smart"),
            ("bodyNum", "1", "single"),
            ("bodyNum", "2", "double"),
        ),
        default_operation="double",
        # Smart mode picks its own setpoint (jumped straight to the appliance's max on the
        # real unit) and the app itself disables manual entry while it is active.
        temp_locked_when=("cloudSmart", "1"),
    ),
    sensors=(
        SensorSpec("heatStatus", "heat_status", device_class=SensorDeviceClass.ENUM,
                   value_map={"0": "standby", "1": "heating", "2": "keep_warm"},
                   gates=Gates(meaningful_when=~StateIs("power_off"))),
        # Minutes until the setpoint is reached; only meaningful while actively heating —
        # the vendor app itself only shows it in that state.
        SensorSpec("remainTime", "remaining_time", device_class=SensorDeviceClass.DURATION,
                   unit=UnitOfTime.MINUTES, gates=Gates(meaningful_when=StateIs("heating"))),
        # No fault-code table yet — only "0" (no fault) has been observed on real hardware,
        # unlike the dishwasher/oven tables built from a real fault. The binary problem flag
        # below needs no such table, so it ships now; add an ENUM sensor once codes turn up.
    ),
    binary_sensors=(
        BinarySensorSpec("faultCode", "fault", device_class=BinarySensorDeviceClass.PROBLEM,
                         on_values=("0",), invert=True, uid="faultStatus",
                         entity_category=EntityCategory.DIAGNOSTIC),
    ),
)


# =========================================================================================
# 0xAC — Air conditioner → native `climate` platform.
# Note: modelled from the cloud protocol; behaviour not yet verified on hardware.
# =========================================================================================

# The vendor plugin has no state table for the air conditioner either — its screen keys off
# power and the mode independently, so this is the honest minimum rather than a guess.
AIR_CONDITIONER_STATES = (
    StateRule("off", Is("power", "0")),
    StateRule("on", Is("power", "1")),
)

AIR_CONDITIONER = CategorySpec(
    states=AIR_CONDITIONER_STATES,
    device_type="0xAC",
    category="air_conditioner",
    primary_platform=Platform.CLIMATE,
    climate=ClimateConfig(
        hvac_mode_codes={
            "1": "auto", "2": "cool", "3": "dry", "4": "heat", "5": "fan_only",
        },
        fan_modes={"low": "40", "medium": "60", "high": "80", "auto": "102"},
    ),
)


# =========================================================================================
# 0xB1 — Oven → composite (no native HA oven platform exists).
#
# Programme composer (mode / temperature / duration / probe / pre-heat + start) lives in
# ``oven.py``; everything read-only or independently writable is declared here.
# Note: modelled from the cloud protocol; behaviour not yet verified on hardware.
# =========================================================================================

# Cook-mode codes are ``minor + 256 * main`` as carried by the cloud API.
OVEN_PROGRAMS: dict[str, OvenProgram] = {
    "conventional": OvenProgram("21249", min_temp=30, default_temp=180),
    "convection": OvenProgram("21505", default_temp=160),
    "conventional_fan": OvenProgram("24320", default_temp=160),
    "radiant_heat": OvenProgram("26112", min_temp=150, default_temp=180),
    "double_grill_fan": OvenProgram("25344", default_temp=180),
    "double_grill": OvenProgram("26368", default_temp=180),
    "pizza": OvenProgram("22272", default_temp=180),
    "bottom_heat": OvenProgram("20993", default_temp=180),
    "eco": OvenProgram(
        "41472", min_temp=140, max_temp=240, temp_step=20, default_temp=140,
        supports_preheat=False,
    ),
    "fermentation": OvenProgram(
        "22528", min_temp=30, max_temp=50, default_temp=40, supports_preheat=False,
    ),
    "keep_warm": OvenProgram(
        "24064", min_temp=60, max_temp=100, temp_step=1, default_temp=75,
        supports_preheat=False, max_minutes=540,
    ),
    # Defrost is time-only: the cook instruction carries no temperature at all.
    "defrost": OvenProgram("41984", supports_temp=False, supports_preheat=False),
}

# Derived run state; the transform folds the pre-heat phase over the raw appliance state.
_OVEN_MACHINE_STATES = (
    "offline", "off", "standby", "cooking", "preheating", "preheat_finish",
    "cook_complete", "delay", "pause",
)
_OVEN_FAULT = {
    "0": "none", "1": "e01", "2": "e02", "5": "e72", "17": "e03", "255": "d11",
}
# Some models report a single packed status integer instead of the discrete flags below.
_OVEN_SUMMARY = "transportStatusSummary1"

# The oven keeps its whole run state in one field rather than splitting it across a power
# flag and a run flag the way the wash appliances do.
OVEN_STATES = (
    StateRule("fault", _faulted("faultCode", _OVEN_FAULT)),
    StateRule("off", Is("devstatus", "1")),
    StateRule("standby", Is("devstatus", "2")),
    StateRule("cooking", Is("devstatus", "3")),
    StateRule("cook_complete", Is("devstatus", "4")),
    StateRule("delay", Is("devstatus", "5")),
    StateRule("pause", Is("devstatus", "6")),
)
# The cook is under way: setpoint and countdown are live only here. In standby the vendor
# app replaces the whole working block with the programme list.
_OVEN_COOKING = StateIs("cooking", "pause")

OVEN_GUARD = (
    Block(StateIs("off"), "appliance_off"),
    Block(StateIs("fault"), "appliance_fault"),
    Block(Is("childLock", "1"), "child_lock"),
)
_OVEN_POWER_EXEMPT = frozenset({"appliance_off", "appliance_fault", "child_lock"})

OVEN = CategorySpec(
    device_type="0xB1",
    category="oven",
    primary_platform=None,  # no native HA platform — composite
    states=OVEN_STATES,
    guard=OVEN_GUARD,
    oven=OvenConfig(programs=OVEN_PROGRAMS),
    sensors=(
        SensorSpec("devstatus", "machine_status", device_class=SensorDeviceClass.ENUM,
                   transform=TRANSFORM_OVEN_STATUS, options=_OVEN_MACHINE_STATES),
        # The countdown freezes when the cook pauses and holds the last cook's value when
        # it ends, so outside cooking it is not a remaining time at all.
        SensorSpec("cookRemainTime", "remaining_time", device_class=SensorDeviceClass.DURATION,
                   unit=UnitOfTime.MINUTES, transform=TRANSFORM_SECONDS_MINUTES,
                   gates=Gates(meaningful_when=_OVEN_COOKING)),
        # The appliance echoes the working setpoint; it has no independent cavity probe.
        SensorSpec("upperTemp", "working_temperature", device_class=SensorDeviceClass.TEMPERATURE,
                   unit=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
                   gates=Gates(meaningful_when=_OVEN_COOKING)),
        SensorSpec("faultCode", "oven_fault", device_class=SensorDeviceClass.ENUM,
                   value_map=_OVEN_FAULT, entity_category=EntityCategory.DIAGNOSTIC),
    ),
    binary_sensors=(
        BinarySensorSpec("doorStatus", "door", device_class=BinarySensorDeviceClass.DOOR,
                         on_values=("1",), summary_key=_OVEN_SUMMARY, summary_bit=1),
        BinarySensorSpec("faultCode", "fault", device_class=BinarySensorDeviceClass.PROBLEM,
                         on_values=("0",), invert=True, uid="faultStatus",
                         entity_category=EntityCategory.DIAGNOSTIC),
        BinarySensorSpec("preheatState", "preheating", device_class=BinarySensorDeviceClass.HEAT,
                         on_values=("1",), uid="preheatActive",
                         summary_key=_OVEN_SUMMARY, summary_bit=5),
        BinarySensorSpec("preheatState", "preheat_reached",
                         device_class=BinarySensorDeviceClass.HEAT, on_values=("2", "3"),
                         uid="preheatReached", summary_key=_OVEN_SUMMARY, summary_bit=6),
        BinarySensorSpec("waterTankStatus", "water_tank", capability="steam",
                         summary_key=_OVEN_SUMMARY, summary_bit=2,
                         entity_category=EntityCategory.DIAGNOSTIC),
        BinarySensorSpec("lackOfWaterStatus", "water_low",
                         device_class=BinarySensorDeviceClass.PROBLEM,
                         capability="steam", summary_key=_OVEN_SUMMARY, summary_bit=3),
        BinarySensorSpec("changeWaterStatus", "water_change",
                         device_class=BinarySensorDeviceClass.PROBLEM,
                         capability="steam", summary_key=_OVEN_SUMMARY, summary_bit=4),
    ),
    switches=(
        # The appliance refuses child-lock writes while the door stands open.
        SwitchSpec("childLock", "child_lock", on_command={"childLock": "1"},
                   off_command={"childLock": "0"}, capability="child_lock",
                   entity_category=EntityCategory.CONFIG,
                   gates=Gates(blocks=(Block(Is("doorStatus", "1"), "door_open"),))),
    ),
    buttons=(
        ButtonSpec("resume", "resume", command={"power": "3"}),
        ButtonSpec("pause", "pause", command={"power": "6"}),
        ButtonSpec("stop", "stop", command={"power": "2"}),
        ButtonSpec("standby", "standby", command={"power": "1"}),
    ),
)


# Keyed by normalized device_type. New categories are added here.
# =========================================================================================
# 0xDB — Washing machine → composite (Home Assistant has no washer platform).
# Note: modelled from the cloud protocol; behaviour not yet verified on hardware.
# =========================================================================================

_WASHER_RUN_STATE = {
    "0": "power_off", "1": "standby", "2": "running", "3": "pause",
    "4": "finished", "5": "fault",
}
_WASHER_PHASE = {
    "0": "idle", "2": "rinse", "4": "wash", "8": "pre_wash",
    "16": "spin", "32": "auto_weight", "64": "drying",
}
_WASHER_FAULT = {
    "0": "none", "16": "e10", "18": "e12", "33": "e21", "48": "e30",
    "51": "e33", "55": "e37",
}
_WASHER_PROGRAM = {
    "cotton": "0", "cotton_eco": "1", "quick": "2", "mix": "3", "synthetic": "4",
    "wool": "5", "baby": "6", "sport": "7", "rinse_spin": "8", "spin_only": "9",
    "drum_clean": "10", "steam_wash": "11", "wash_dry": "12", "dry": "13",
    "my_cycle": "14",
}
_WASHER_TEMPERATURE = {
    "tap_cold": "1", "20": "2", "30": "3", "40": "4", "60": "5", "70": "7", "90": "8",
}
_WASHER_SPIN = {
    "no_spin": "0", "400": "1", "600": "2", "800": "3", "1000": "4", "1200": "5",
    "1400": "6",
}
_WASHER_DRY = {
    "no_dry": "0", "auto": "1", "auto_extra": "2", "auto_less": "3",
}

# The washer splits its state across a power flag and a run state, and reports "0" in the
# run state as well when it is off — both spellings have to resolve to the same thing.
WASHER_STATES = (
    StateRule("power_off", Is("power", "0") | Is("runState", "0")),
    StateRule("fault", Is("runState", "5") | _faulted("faultCode", _WASHER_FAULT)),
    StateRule("standby", Is("runState", "1")),
    StateRule("running", Is("runState", "2")),
    StateRule("pause", Is("runState", "3")),
    StateRule("finished", Is("runState", "4")),
)
_WASHER_RUNNING = StateIs("running", "pause")

WASHER_GUARD = (
    Block(StateIs("power_off"), "appliance_off"),
    Block(StateIs("fault"), "appliance_fault"),
    Block(Is("childLock", "1"), "child_lock"),
)
_WASHER_POWER_EXEMPT = frozenset({"appliance_off", "appliance_fault", "child_lock"})

WASHER = CategorySpec(
    device_type="0xDB",
    states=WASHER_STATES,
    guard=WASHER_GUARD,
    category="washer",
    primary_platform=None,
    sensors=(
        SensorSpec("runState", "machine_status", device_class=SensorDeviceClass.ENUM,
                   value_map=_WASHER_RUN_STATE),
        # The phase is the drum's current job; when no cycle runs there is no phase, and
        # the vendor's progress bar renders nothing at all in those states.
        SensorSpec("washingStatus", "washing_phase", device_class=SensorDeviceClass.ENUM,
                   value_map=_WASHER_PHASE,
                   gates=Gates(meaningful_when=_WASHER_RUNNING)),
        SensorSpec("faultCode", "fault", device_class=SensorDeviceClass.ENUM,
                   value_map=_WASHER_FAULT, entity_category=EntityCategory.DIAGNOSTIC),
        SensorSpec("remainTime", "remaining_time", device_class=SensorDeviceClass.DURATION,
                   unit=UnitOfTime.MINUTES,
                   gates=Gates(meaningful_when=_WASHER_RUNNING)),
        SensorSpec("washTimeFromClean", "washes_since_clean",
                   state_class=SensorStateClass.TOTAL_INCREASING,
                   entity_category=EntityCategory.DIAGNOSTIC,
                   entity_registry_enabled_default=False),
    ),
    binary_sensors=(
        BinarySensorSpec("detergentShortage", "detergent_low",
                         device_class=BinarySensorDeviceClass.PROBLEM,
                         capability="auto_dose_supported"),
        BinarySensorSpec("softenerLiquidShortage", "softener_low",
                         device_class=BinarySensorDeviceClass.PROBLEM,
                         capability="auto_dose_supported"),
        BinarySensorSpec("barrelSelfClean", "drum_clean_due",
                         device_class=BinarySensorDeviceClass.PROBLEM),
        BinarySensorSpec("remoteControl", "remote_control",
                         device_class=BinarySensorDeviceClass.CONNECTIVITY,
                         entity_category=EntityCategory.DIAGNOSTIC,
                         entity_registry_enabled_default=False),
    ),
    switches=(
        SwitchSpec("power", "power", on_command={"power": "1"},
                   off_command={"power": "0"},
                   gates=Gates(exempt=_WASHER_POWER_EXEMPT)),
        SwitchSpec("startPause", "running", on_command={"startPause": "1"},
                   off_command={"startPause": "0"}),
        SwitchSpec("addRinse", "extra_rinse", on_command={"addRinse": "1"},
                   off_command={"addRinse": "0"}, capability="extra_rinse_editable"),
        SwitchSpec("addSpeedWash", "speed_wash", on_command={"addSpeedWash": "1"},
                   off_command={"addSpeedWash": "0"}, capability="speed_wash_supported"),
        SwitchSpec("autoDose", "auto_dose", on_command={"autoDose": "1"},
                   off_command={"autoDose": "0"}, capability="auto_dose_supported",
                   entity_category=EntityCategory.CONFIG),
    ),
    selects=(
        SelectSpec("cycle", "program", command_key="cycle", options=_WASHER_PROGRAM,
                   capability="program_supported"),
        SelectSpec("temp", "wash_temperature", command_key="temp",
                   options=_WASHER_TEMPERATURE, capability="temp_editable"),
        SelectSpec("speed", "spin_speed", command_key="speed", options=_WASHER_SPIN),
        SelectSpec("dry", "dry_level", command_key="dry", options=_WASHER_DRY,
                   capability="drying_supported"),
    ),
    numbers=(
        NumberSpec("orderTime", "delay_start", command_key="orderTime",
                   native_min=0, native_max=1440, step=30),
    ),
)


CATEGORIES: dict[str, CategorySpec] = {
    DISHWASHER.device_type: DISHWASHER,
    LAMP.device_type: LAMP,
    WATER_HEATER.device_type: WATER_HEATER,
    AIR_CONDITIONER.device_type: AIR_CONDITIONER,
    OVEN.device_type: OVEN,
    WASHER.device_type: WASHER,
}


def get_category(device_type: str) -> CategorySpec | None:
    """Return the spec for a device type, or None if the category is not yet supported."""
    return CATEGORIES.get(device_type)
