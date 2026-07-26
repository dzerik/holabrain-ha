"""Capability resolution — one chain of strategies per appliance family.

There is no single cloud mechanism that says what an appliance can do. Each family
advertises its features differently, so this module models capability discovery as an
ordered **chain of resolvers** whose results are merged into one
:class:`~..dto.capability.CapabilityProfile`:

======================  ======================================================================
Resolver                Source of truth
======================  ======================================================================
:class:`StaticResolver`         per-model tables that are constant for a product family
:class:`DictGetResolver`        the cloud capability dictionary (``function/dict/get``)
:class:`BitfieldResolver`       the packed ``dynamicAttr`` descriptor in the device metadata
:class:`StatusPresenceResolver` which keys the device actually reports in its status
======================  ======================================================================

Chain order is meaningful: static tables lay down defaults, the cloud dictionary and the
packed descriptor refine them, and status presence adds whatever the device demonstrably
reports. A resolver that has nothing to say returns ``None`` and the chain moves on; a
resolver that fails is skipped unless it is marked ``required``.

Everything here is dependency-injected and free of module-level mutable state: the tables
are immutable constants, the cloud access goes through the injected
:class:`~..auth.manager.AuthManager`, and the status snapshot comes from an injected
provider so a chain can be exercised without any network at all.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Protocol, runtime_checkable

from ..auth.manager import AuthManager
from ..bitfield import AC_DYNAMIC_ATTR_LAYOUT, BitfieldLayout, decode_bitfield
from ..const import EP_FUNCTION_DICT
from ..dto.capability import CapabilityProfile, empty_profile, parse_capability
from ..exceptions import ApiError, DollinError

_LOGGER = logging.getLogger(__name__)

StatusProvider = Callable[[str], Awaitable[Mapping[str, Any]]]
Clock = Callable[[], float]

__all__ = [
    "AC_FLAG_ALIASES",
    "BitfieldResolver",
    "CapabilityApi",
    "CapabilityResolver",
    "DictGetResolver",
    "PresenceRule",
    "ResolutionContext",
    "ResolverChain",
    "StaticResolver",
    "StaticVariant",
    "StatusPresenceResolver",
    "build_default_chains",
]


# =========================================================================================
# Chain plumbing
# =========================================================================================


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    """Everything a resolver may look at, gathered once per device."""

    device_type: str
    model: str
    thing_code: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    status: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class CapabilityResolver(Protocol):
    """One capability source."""

    name: str
    required: bool

    async def async_resolve(self, ctx: ResolutionContext) -> CapabilityProfile | None:
        """Return a partial profile, or ``None`` if this source does not apply."""


class ResolverChain:
    """Runs resolvers in order and merges their partial profiles."""

    def __init__(
        self,
        resolvers: Sequence[CapabilityResolver],
        *,
        clock: Clock | None = None,
    ) -> None:
        self._resolvers = tuple(resolvers)
        self._clock = clock or time.time

    @property
    def resolvers(self) -> tuple[CapabilityResolver, ...]:
        return self._resolvers

    async def async_resolve(self, ctx: ResolutionContext) -> CapabilityProfile:
        """Resolve ``ctx`` into a single profile.

        Raises the last error only if a ``required`` resolver failed and nothing else
        produced anything — that is the case where falling back to a cached profile is
        better than publishing an empty one.
        """
        now = self._clock()
        profile = empty_profile(
            model=ctx.model, device_type=ctx.device_type, fetched_at=now
        )
        produced = False
        required_error: DollinError | None = None

        for resolver in self._resolvers:
            try:
                partial = await resolver.async_resolve(ctx)
            except DollinError as err:
                if getattr(resolver, "required", False):
                    required_error = err
                _LOGGER.debug(
                    "capability resolver %s skipped for %s/%s: %s",
                    resolver.name,
                    ctx.device_type,
                    ctx.model or ctx.thing_code,
                    err,
                )
                continue
            if partial is None:
                continue
            produced = True
            profile = profile.merge(
                CapabilityProfile(
                    model=partial.model or ctx.model,
                    features=partial.features,
                    params=partial.params,
                    device_type=partial.device_type or ctx.device_type,
                    present_fields=partial.present_fields,
                    sources=partial.sources or (resolver.name,),
                    fetched_at=now,
                )
            )

        if required_error is not None and not produced:
            raise required_error
        return profile


# =========================================================================================
# Strategy 1 — cloud capability dictionary (device type 0xE1 and any family that answers)
# =========================================================================================


class DictGetResolver:
    """Reads the per-model capability dictionary from the cloud.

    Two response shapes are handled: a bare list of feature tokens, and an object carrying
    ``module`` (the feature list) plus a ``sn8Config`` block that narrows the program list
    (``moduleParams.mode``) and selects the door-opening stage map (``openType``).
    """

    name = "dict"

    def __init__(self, auth: AuthManager, *, required: bool = True) -> None:
        self._auth = auth
        self.required = required

    async def async_resolve(self, ctx: ResolutionContext) -> CapabilityProfile | None:
        if not ctx.model:
            return None
        return await self.async_get_profile(ctx.model, device_type=ctx.device_type)

    async def async_get_profile(
        self, model: str, *, device_type: str = ""
    ) -> CapabilityProfile:
        """Fetch and parse the capability dictionary for ``model`` (an 8-char ``sn8``)."""
        data = await self._auth.oem(EP_FUNCTION_DICT, {"model": model})
        items, extra = _split_dict_payload(data.get("data"), model)
        return parse_capability(
            items, model=model, device_type=device_type, extra_params=extra
        )


def _split_dict_payload(raw: Any, model: str) -> tuple[list[Any], dict[str, Any]]:
    """Normalize the capability-dictionary body into ``(feature list, extra params)``."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as err:
            raise ApiError(f"capability dict for {model} was not valid JSON") from err

    if isinstance(raw, list):
        return raw, {}

    if isinstance(raw, dict):
        items = raw.get("module")
        if not isinstance(items, list):
            items = []
        extra: dict[str, Any] = {}
        config = raw.get("sn8Config")
        if isinstance(config, dict):
            params = config.get("moduleParams")
            if isinstance(params, dict) and isinstance(params.get("mode"), list):
                extra["mode_subset"] = [str(m) for m in params["mode"]]
            if config.get("openType") is not None:
                extra["open_type"] = str(config["openType"])
        if not items and not extra:
            raise ApiError(f"capability dict for {model} carried no feature list")
        return items, extra

    raise ApiError(f"capability dict for {model} had unexpected type")


# =========================================================================================
# Strategy 2 — packed capability descriptor (device type 0xAC)
# =========================================================================================

# Raw descriptor field -> normalized capability flag. Both names end up in the profile, so a
# descriptor can be gated either way; the normalized name is the one the registry uses.
AC_FLAG_ALIASES: Final[Mapping[str, str]] = {
    "isEcoFunc": "eco",
    "is8DegreeHeatFunc": "heat_8c",
    "isStrong": "boost",
    "iselectricityStauts": "power_meter",
    "isColdandWarm": "heat",
    "isSmoothWind": "stepless_fan",
    "isDecimalTemp": "decimal_temp",
    "isSwingWind": "swing",
    "isSelfCleaning": "self_clean",
    "isSmartEye": "smart_eye",
    "isWindNOTBreezed": "wind_not_breezed",
    "isWindBreezed": "wind_breezed",
    "isLightControl": "led_display",
    "isfourLouver": "four_louver",
    "isWaterTankControl": "water_tank",
    "iswindSoftCtrl": "wind_soft",
    "isWetted": "evaporator_dry",
    "isAnion": "anion",
    "isTwinsDevice": "twin_unit",
    "iswindNoneCtrl": "no_wind_sense",
    "notWindStatus": "up_direct_blow",
    "isRoundWindStatus": "round_wind",
    "isUnDirectBlow": "prevent_straight_wind",
    "isFilterScreen": "filter_screen",
    "isFahrenheit": "fahrenheit",
    "isBuzzer": "buzzer",
}

# Sanity window for the trailing temperature blocks; anything outside is treated as unset.
_TEMP_SANE: Final = (5, 40)
_TEMP_DEFAULTS: Final = (17, 30)


class BitfieldResolver:
    """Decodes the packed capability descriptor carried in the device metadata."""

    name = "bitfield"
    required = False

    def __init__(
        self,
        *,
        layout: BitfieldLayout = AC_DYNAMIC_ATTR_LAYOUT,
        aliases: Mapping[str, str] = AC_FLAG_ALIASES,
        metadata_keys: Sequence[str] = ("dynamicAttr", "manualDynamicAttr"),
        temp_defaults: tuple[int, int] = _TEMP_DEFAULTS,
        lsb_first: bool = True,
    ) -> None:
        self._layout = layout
        self._aliases = dict(aliases)
        self._metadata_keys = tuple(metadata_keys)
        self._temp_defaults = temp_defaults
        self._lsb_first = lsb_first

    async def async_resolve(self, ctx: ResolutionContext) -> CapabilityProfile | None:
        blob = _first_str(ctx.metadata, self._metadata_keys)
        temp_min, temp_max = self._temp_defaults
        if blob is None:
            # No descriptor: still publish the default temperature window so the climate
            # entity has a sane range instead of none at all.
            return CapabilityProfile(
                model=ctx.model,
                features=frozenset(),
                params=_frozen({"temp_min": temp_min, "temp_max": temp_max}),
                device_type=ctx.device_type,
                sources=(self.name,),
            )

        decoded = decode_bitfield(blob, self._layout, lsb_first=self._lsb_first)
        features: set[str] = set()
        for raw_name, value in decoded.items():
            if raw_name.startswith("isReserve") or not value:
                continue
            features.add(raw_name)
            alias = self._aliases.get(raw_name)
            if alias:
                features.add(alias)

        params: dict[str, Any] = {
            "temp_min": _sane_temp(decoded.get("tempMinimum"), temp_min),
            "temp_max": _sane_temp(decoded.get("tempMaximum"), temp_max),
            "temp_step": 0.5 if "decimal_temp" in features else 1,
        }
        return CapabilityProfile(
            model=ctx.model,
            features=frozenset(features),
            params=_frozen(params),
            device_type=ctx.device_type,
            sources=(self.name,),
        )


def _sane_temp(value: Any, fallback: int) -> int:
    if isinstance(value, int) and _TEMP_SANE[0] <= value <= _TEMP_SANE[1]:
        return value
    return fallback


# =========================================================================================
# Strategy 3 — status-field presence (every family)
# =========================================================================================


@dataclass(frozen=True, slots=True)
class PresenceRule:
    """Raise ``flag`` when the device reports the listed status keys."""

    flag: str
    keys: tuple[str, ...]
    require_all: bool = False

    def matches(self, status: Mapping[str, Any]) -> bool:
        hits = (key in status for key in self.keys)
        return all(hits) if self.require_all else any(hits)


class StatusPresenceResolver:
    """Derives capabilities from the keys a device actually reports.

    Several families have no capability negotiation whatsoever; the only honest signal is
    whether the appliance ever reported the corresponding status key. Both the derived flags
    and the raw key set are published, and the key set is unioned into the cached profile by
    the caller so a truncated response can never remove entities.
    """

    name = "presence"
    required = False

    def __init__(
        self,
        rules: Iterable[PresenceRule] = (),
        *,
        status_provider: StatusProvider | None = None,
    ) -> None:
        self._rules = tuple(rules)
        self._status_provider = status_provider

    async def async_resolve(self, ctx: ResolutionContext) -> CapabilityProfile | None:
        status = ctx.status
        if not status and self._status_provider is not None and ctx.thing_code:
            status = await self._status_provider(ctx.thing_code)
        if not status:
            return None

        features = {rule.flag for rule in self._rules if rule.matches(status)}
        return CapabilityProfile(
            model=ctx.model,
            features=frozenset(features),
            device_type=ctx.device_type,
            present_fields=frozenset(str(key) for key in status),
            sources=(self.name,),
        )


# =========================================================================================
# Strategy 4 — static per-model tables
# =========================================================================================


@dataclass(frozen=True, slots=True)
class StaticVariant:
    """Capabilities that are constant for a model (or a group of models)."""

    features: frozenset[str] = frozenset()
    params: Mapping[str, Any] = field(default_factory=dict)


class StaticResolver:
    """Applies a family-wide baseline plus the matching per-model variant.

    Model matching is exact first, then longest-prefix, so a table can key either whole
    model codes or a family prefix.
    """

    name = "static"
    required = False

    def __init__(
        self,
        base: StaticVariant | None = None,
        variants: Mapping[str, StaticVariant] | None = None,
    ) -> None:
        self._base = base or StaticVariant()
        self._variants = dict(variants or {})

    def variant_for(self, model: str) -> StaticVariant | None:
        if not model:
            return None
        exact = self._variants.get(model)
        if exact is not None:
            return exact
        candidates = [key for key in self._variants if model.startswith(key)]
        if not candidates:
            return None
        return self._variants[max(candidates, key=len)]

    async def async_resolve(self, ctx: ResolutionContext) -> CapabilityProfile | None:
        variant = self.variant_for(ctx.model)
        features = set(self._base.features)
        params = dict(self._base.params)
        if variant is not None:
            features |= variant.features
            params.update(variant.params)
        if not features and not params:
            return None
        return CapabilityProfile(
            model=ctx.model,
            features=frozenset(features),
            params=_frozen(params),
            device_type=ctx.device_type,
            sources=(self.name,),
        )


# =========================================================================================
# Default tables per appliance family
# =========================================================================================

# --- 0x13 lamp ---------------------------------------------------------------------------
_LAMP_STATIC: Final = StaticResolver(
    base=StaticVariant(
        features=frozenset({"brightness", "color_temp", "scene_modes"}),
        params={
            "scene_modes": ["manual", "life", "read", "soft", "cinema", "night"],
            "kelvin_min": 2700,
            "kelvin_max": 6500,
        },
    ),
    variants={
        # Combined fan-light fixtures move the lamp onto its own power key and add a fan.
        "79010863": StaticVariant(features=frozenset({"fan_light", "thermostat"})),
    },
)
_LAMP_PRESENCE: Final = (
    PresenceRule("color_temp", ("colorTemp", "color_temperature")),
    PresenceRule("brightness", ("bright", "brightness")),
    PresenceRule("fan_light", ("fan_power", "led_power")),
    PresenceRule("fan_speed", ("fan_speed",)),
    PresenceRule("oscillation", ("oscillating_switch", "en_oscillating_switch")),
    PresenceRule("thermostat", ("const_temperature_value", "indoor_temperature")),
    PresenceRule("delay_off", ("delay_light_off",)),
)

# --- 0xAC air conditioner ----------------------------------------------------------------
_AC_PRESENCE: Final = (
    PresenceRule("outdoor_temp", ("outTemp", "outdoor_temperature")),
    PresenceRule("indoor_humidity", ("indoor_humidity", "humidity")),
    PresenceRule("energy_total", ("total_elec",)),
    PresenceRule("power_meter", ("real_time_power_value", "real_time_power")),
    PresenceRule("fresh_air", ("new_wind_machine",)),
    PresenceRule("prevent_super_cool", ("prevent_super_cool",)),
)

# --- 0xB1 oven ---------------------------------------------------------------------------
_OVEN_STATIC: Final = StaticResolver(
    base=StaticVariant(
        features=frozenset({"menu_cook", "preheat", "degree", "child_lock"}),
        params={
            # Modes that accept no temperature / no pre-heat. The platform strips the
            # corresponding keys from the start command and hides the controls.
            "non_degree_modes": ["defrost"],
            "non_preheat_modes": ["eco", "defrost", "fermentation", "keep_warm"],
        },
    )
)
_OVEN_PRESENCE: Final = (
    PresenceRule("steam", ("waterTankStatus", "lackOfWaterStatus", "changeWaterStatus")),
    PresenceRule("food_probe", ("probeTemp",)),
    PresenceRule("child_lock", ("childLock",)),
    PresenceRule("status_summary", ("transportStatusSummary1",)),
)

# --- 0xDB washer -------------------------------------------------------------------------
_WASHER_STATIC: Final = StaticResolver(
    base=StaticVariant(
        features=frozenset({"program", "temperature", "spin", "extra_rinse", "child_lock"}),
        params={
            # Program-scoped visibility tables; the platform consults them at runtime.
            "temp_non_show": ["dry", "rinse_spin", "spin_only"],
            "dry_level_non_show": ["quick", "wool", "rinse_spin", "spin_only", "drum_clean"],
            "speed_wash_show": ["steam_wash", "synthetic", "mix", "cotton", "my_cycle"],
            "auto_dose_non_show": ["dry", "spin_only", "drum_clean"],
            "extra_rinse_non_show": ["dry", "spin_only", "drum_clean"],
        },
    ),
    variants={
        "38127413": StaticVariant(params={"capacity_kg": 12}),
        "38127414": StaticVariant(params={"capacity_kg": 10}),
    },
)
_WASHER_PRESENCE: Final = (
    PresenceRule("auto_dose", ("complianceDose", "washingDose")),
    PresenceRule("detergent_lack", ("detergentShortage",)),
    PresenceRule("softener_lack", ("softenerLiquidShortage",)),
    PresenceRule("drying", ("dry",)),
    PresenceRule("speed_wash", ("addSpeedWash",)),
    PresenceRule("remote_control", ("remoteControl",)),
    PresenceRule("self_clean", ("barrelSelfClean",)),
    PresenceRule("child_lock", ("funcOption",)),
)

# --- 0xE2 water heater -------------------------------------------------------------------
_WATER_HEATER_STATIC: Final = StaticResolver(
    base=StaticVariant(
        features=frozenset({"power", "target_temp", "mode_smart_eco"}),
        params={"temp_min": 30, "temp_max": 80},
    ),
    variants={
        "51020ED1": StaticVariant(
            features=frozenset({"timing", "disinfect", "dual_tank", "high_temp"}),
            params={"temp_min": 35, "temp_max": 75},
        ),
        "510214FN": StaticVariant(
            features=frozenset({"heat_half", "eplus", "efficient", "night", "sterilization"}),
            params={"temp_min": 30, "temp_max": 75},
        ),
        "510214HB": StaticVariant(
            features=frozenset(
                {
                    "heat_half",
                    "eplus",
                    "efficient",
                    "night",
                    "sterilization",
                    "protect",
                    "smart_sterilize",
                    "frequency_hot",
                }
            ),
            params={"temp_min": 30, "temp_max": 75},
        ),
        "5102152H": StaticVariant(
            features=frozenset(
                {
                    "heat_half",
                    "eplus",
                    "efficient",
                    "night",
                    "sterilization",
                    "protect",
                    "smart_sterilize",
                    "frequency_hot",
                }
            ),
            params={"temp_min": 30, "temp_max": 75},
        ),
        "51001938": StaticVariant(params={"temp_min": 30, "temp_max": 75}),
    },
)
_WATER_HEATER_PRESENCE: Final = (
    PresenceRule("current_temp", ("cur_temperature",)),
    PresenceRule("dual_tank", ("bodyNum", "singleOrDouble")),
    PresenceRule("high_temp", ("highTemp",)),
    PresenceRule("smart", ("cloudSmart",)),
    PresenceRule("eco", ("eco",)),
    PresenceRule("water_level", ("heat_water_level",)),
)

# --- 0xE1 dishwasher ---------------------------------------------------------------------
_DISHWASHER_PRESENCE: Final = (
    PresenceRule("statistics", ("totalwashTimes", "totalWaterVol", "totalElectricVol")),
    PresenceRule("auto_open", ("autoDoorOpen",)),
    PresenceRule("salt", ("salt", "softWaterGear")),
    PresenceRule("rinse_aid", ("brightenAgent", "distributorGear")),
)


def build_default_chains(
    auth: AuthManager,
    *,
    status_provider: StatusProvider | None = None,
    clock: Clock | None = None,
    probe_dict_get: bool = False,
) -> dict[str, ResolverChain]:
    """Build the per-family resolver chains.

    ``probe_dict_get`` additionally asks the cloud capability dictionary for families whose
    features are otherwise resolved from tables and status presence. It is off by default:
    those families are not known to answer, and a wasted request per model on every
    revalidation is worse than the (currently zero) extra information.
    """

    def presence(rules: Sequence[PresenceRule]) -> StatusPresenceResolver:
        return StatusPresenceResolver(rules, status_provider=status_provider)

    def optional_dict() -> list[CapabilityResolver]:
        return [DictGetResolver(auth, required=False)] if probe_dict_get else []

    chains = {
        # Cloud capability dictionary is authoritative here; presence only adds sensors.
        "0xE1": ResolverChain(
            [DictGetResolver(auth, required=True), presence(_DISHWASHER_PRESENCE)],
            clock=clock,
        ),
        # Packed descriptor drives the optional controls; presence gates optional sensors.
        "0xAC": ResolverChain(
            [BitfieldResolver(), presence(_AC_PRESENCE)],
            clock=clock,
        ),
        "0x13": ResolverChain(
            [_LAMP_STATIC, *optional_dict(), presence(_LAMP_PRESENCE)],
            clock=clock,
        ),
        "0xB1": ResolverChain(
            [_OVEN_STATIC, *optional_dict(), presence(_OVEN_PRESENCE)],
            clock=clock,
        ),
        "0xDB": ResolverChain(
            [_WASHER_STATIC, *optional_dict(), presence(_WASHER_PRESENCE)],
            clock=clock,
        ),
        "0xE2": ResolverChain(
            [_WATER_HEATER_STATIC, *optional_dict(), presence(_WATER_HEATER_PRESENCE)],
            clock=clock,
        ),
    }
    return chains


def build_fallback_chain(
    *,
    status_provider: StatusProvider | None = None,
    clock: Clock | None = None,
) -> ResolverChain:
    """Chain for families that are not modelled yet: report only what the device shows."""
    return ResolverChain(
        [StatusPresenceResolver((), status_provider=status_provider)], clock=clock
    )


# =========================================================================================
# Public API
# =========================================================================================


class CapabilityApi:
    """Resolve what a specific appliance supports."""

    def __init__(
        self,
        auth: AuthManager,
        *,
        chains: Mapping[str, ResolverChain] | None = None,
        fallback: ResolverChain | None = None,
        status_provider: StatusProvider | None = None,
        clock: Clock | None = None,
        probe_dict_get: bool = False,
    ) -> None:
        self._auth = auth
        self._chains = dict(
            chains
            if chains is not None
            else build_default_chains(
                auth,
                status_provider=status_provider,
                clock=clock,
                probe_dict_get=probe_dict_get,
            )
        )
        self._fallback = fallback or build_fallback_chain(
            status_provider=status_provider, clock=clock
        )
        self._dict_resolver = DictGetResolver(auth)

    def chain_for(self, device_type: str) -> ResolverChain:
        """Return the resolver chain for a device type (never ``None``)."""
        return self._chains.get(device_type, self._fallback)

    def supports_device_type(self, device_type: str) -> bool:
        return device_type in self._chains

    async def async_resolve(
        self,
        *,
        device_type: str,
        model: str,
        thing_code: str = "",
        metadata: Mapping[str, Any] | None = None,
        status: Mapping[str, Any] | None = None,
    ) -> CapabilityProfile:
        """Resolve the full capability profile for one device."""
        ctx = ResolutionContext(
            device_type=device_type,
            model=model,
            thing_code=thing_code,
            metadata=metadata or {},
            status=status or {},
        )
        return await self.chain_for(device_type).async_resolve(ctx)

    async def async_get_profile(self, model: str) -> CapabilityProfile:
        """Fetch only the cloud capability dictionary for ``model`` (an 8-char ``sn8``)."""
        return await self._dict_resolver.async_get_profile(model)


def _frozen(params: Mapping[str, Any]) -> MappingProxyType:
    return MappingProxyType(dict(params))


def _first_str(source: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
