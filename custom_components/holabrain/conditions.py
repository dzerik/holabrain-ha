"""Declaration language for "this reading has no meaning in the appliance's current state".

An appliance reports every field it owns, all the time, whether or not the field currently
means anything. A switched-off dishwasher still reports a wash programme and a remaining
time — leftovers from the last cycle that the vendor's own app refuses to display: it
substitutes a table estimate labelled "estimated time" and drops the stage block entirely.
Surfacing those numbers in Home Assistant is worse than showing nothing, because a stale
value looks live and automations act on it.

This module is the vocabulary for saying so, once, declaratively:

* :class:`Cond` — a predicate over a device snapshot (``Is``, ``Between``, ``Missing``, …).
* :class:`StateRule` — one arm of a category's state machine; the appliance families all
  have one, and this is the single place it is written down.
* :class:`Gates` — where a condition attaches to an entity: blank the value, or refuse a
  write with a reason.

Everything here is pure data plus an evaluator. It imports neither Home Assistant nor
``aiodollin``: the registry declares conditions, the entity layer supplies the context and
decides what a failed condition means for Home Assistant.

**Three-valued evaluation.** ``holds()`` returns ``True``, ``False`` or ``None``, where
``None`` means "the snapshot does not carry the key this asks about". Push frames are a
subset of the appliance's state, so an absent key is normal and must not be confused with a
definite answer. The connectives follow Kleene logic — ``AllOf`` is ``False`` if any part is
``False`` even when another is unknown — so a verdict never depends on the order the parts
were written in. Callers resolve the remaining ``None`` explicitly, and every caller in this
integration resolves it the same way: fail open. An incomplete snapshot never blanks a
reading and never refuses a command.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

# Virtual keys. These are not fields the appliance reports; the context resolves them.
KEY_STATE = "@state"
"""The category's own state-machine state — see :func:`resolve_state`."""

KEY_STAGED = "@staged:"
"""Prefix for a value staged locally by a composer control but not yet submitted."""


def staged(key: str) -> str:
    """The virtual key holding the locally staged value of ``key``."""
    return f"{KEY_STAGED}{key}"


class GateContext(Protocol):
    """What a condition may ask about the device it is being evaluated against."""

    def value(self, key: str) -> str | None:
        """Current value of a status key or virtual key; ``None`` if the snapshot lacks it."""

    def program_allows(self, flag: str, exclusion_param: str | None) -> bool:
        """Whether the programme being composed accepts ``flag`` at all.

        Some programmes have no temperature (a defrost is time-only) and some appliances
        exclude pre-heat per model. That is a property of the chosen programme rather than
        of the appliance's state, so it is answered by the entity rather than the snapshot.
        """


class Cond(ABC):
    """A three-valued predicate over a device snapshot."""

    __slots__ = ()

    @abstractmethod
    def holds(self, ctx: GateContext) -> bool | None:
        """``True`` / ``False``, or ``None`` when the snapshot cannot answer."""

    def keys(self) -> frozenset[str]:
        """Every key this condition reads. Used to validate the registry at import."""
        return frozenset()

    def state_names(self) -> frozenset[str]:
        """State names this condition compares ``@state`` against.

        The registry checks these against the category's declared rules: a mistyped state
        name is otherwise invisible, because fail-open turns it into a silently passing
        gate rather than an error.
        """
        return frozenset()

    def __and__(self, other: Cond) -> Cond:
        return AllOf((self, other))

    def __or__(self, other: Cond) -> Cond:
        return AnyOf((self, other))

    def __invert__(self) -> Cond:
        return Not(self)


@dataclass(frozen=True, init=False)
class Is(Cond):
    """``key`` currently holds one of ``values``.

    Compared as strings, which is how the cloud reports every field — including numbers.
    """

    key: str
    values: frozenset[str]

    def __init__(self, key: str, *values: str) -> None:
        if not values:
            raise ValueError(f"Is({key!r}) needs at least one value to compare against")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "values", frozenset(values))

    def holds(self, ctx: GateContext) -> bool | None:
        current = ctx.value(self.key)
        return None if current is None else current in self.values

    def keys(self) -> frozenset[str]:
        return frozenset({self.key})

    def state_names(self) -> frozenset[str]:
        return self.values if self.key == KEY_STATE else frozenset()


@dataclass(frozen=True)
class Between(Cond):
    """``key`` is a number inside a plausibility window.

    Appliances report placeholders out of physical range (an air conditioner sends its
    outdoor temperature as a sentinel when no outdoor unit answers), and a sentinel shown
    as a temperature is indistinguishable from a real reading.
    """

    key: str
    low: float
    high: float

    def holds(self, ctx: GateContext) -> bool | None:
        raw = ctx.value(self.key)
        if raw is None:
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            # A non-numeric value is a definite answer: it is not inside the window.
            return False
        return self.low <= number <= self.high

    def keys(self) -> frozenset[str]:
        return frozenset({self.key})


@dataclass(frozen=True)
class Missing(Cond):
    """``key`` carries nothing at all.

    Never unknown: "the snapshot has no value here" is exactly what this asks.
    """

    key: str

    def holds(self, ctx: GateContext) -> bool:
        return ctx.value(self.key) is None

    def keys(self) -> frozenset[str]:
        return frozenset({self.key})


@dataclass(frozen=True)
class ProgramAllows(Cond):
    """The programme being composed accepts ``flag`` (``"temp"``, ``"preheat"``, …)."""

    flag: str
    exclusion_param: str | None = None

    def holds(self, ctx: GateContext) -> bool:
        return ctx.program_allows(self.flag, self.exclusion_param)


@dataclass(frozen=True)
class Not(Cond):
    inner: Cond

    def holds(self, ctx: GateContext) -> bool | None:
        verdict = self.inner.holds(ctx)
        return None if verdict is None else not verdict

    def keys(self) -> frozenset[str]:
        return self.inner.keys()

    def state_names(self) -> frozenset[str]:
        return self.inner.state_names()


@dataclass(frozen=True)
class AllOf(Cond):
    """Kleene conjunction: one definite ``False`` decides, whatever else is unknown."""

    parts: tuple[Cond, ...]

    def holds(self, ctx: GateContext) -> bool | None:
        verdicts = [part.holds(ctx) for part in self.parts]
        if any(verdict is False for verdict in verdicts):
            return False
        return None if any(verdict is None for verdict in verdicts) else True

    def keys(self) -> frozenset[str]:
        return frozenset().union(*(part.keys() for part in self.parts))

    def state_names(self) -> frozenset[str]:
        return frozenset().union(*(part.state_names() for part in self.parts))


@dataclass(frozen=True)
class AnyOf(Cond):
    """Kleene disjunction: one definite ``True`` decides, whatever else is unknown."""

    parts: tuple[Cond, ...]

    def holds(self, ctx: GateContext) -> bool | None:
        verdicts = [part.holds(ctx) for part in self.parts]
        if any(verdict is True for verdict in verdicts):
            return True
        return None if any(verdict is None for verdict in verdicts) else False

    def keys(self) -> frozenset[str]:
        return frozenset().union(*(part.keys() for part in self.parts))

    def state_names(self) -> frozenset[str]:
        return frozenset().union(*(part.state_names() for part in self.parts))


def StateIs(*names: str) -> Cond:
    """Sugar for "the category's state machine is currently in one of ``names``"."""
    return Is(KEY_STATE, *names)


def holds_or(condition: Cond | None, ctx: GateContext, *, default: bool) -> bool:
    """Evaluate ``condition``, resolving "unknown" to ``default``.

    Every gate in this integration passes ``default=True``: an incomplete snapshot must not
    blank a reading or refuse a command. Making that explicit at each call site — rather
    than burying it in the evaluator — keeps the policy visible where it is chosen.
    """
    if condition is None:
        return default
    verdict = condition.holds(ctx)
    return default if verdict is None else verdict


@dataclass(frozen=True)
class Block:
    """One ordered refusal: while ``when`` holds, a write is refused citing ``reason``.

    ``reason`` is a translation key under ``exceptions`` in ``strings.json``. Refusing with
    a reason is deliberately preferred over marking the entity unavailable: Home Assistant
    drops unavailable entities from a service call's target list, so an automation would be
    told it succeeded while nothing happened.
    """

    when: Cond
    reason: str


@dataclass(frozen=True)
class Gates:
    """Where a condition attaches to one entity.

    * ``meaningful_when`` false → the reading is reported as unknown rather than as a
      leftover from the last cycle.
    * ``blocks`` → write-time refusals, evaluated in order, first match wins.
    * ``exempt`` → reasons from the category-wide guard that this control ignores. The
      power control is the archetype: it must stay usable exactly when the appliance is
      off, faulted or its door is open.
    """

    meaningful_when: Cond | None = None
    blocks: tuple[Block, ...] = ()
    exempt: frozenset[str] = frozenset()

    def keys(self) -> frozenset[str]:
        parts = [self.meaningful_when] if self.meaningful_when is not None else []
        parts += [block.when for block in self.blocks]
        return frozenset().union(*(part.keys() for part in parts)) if parts else frozenset()

    def state_names(self) -> frozenset[str]:
        parts = [self.meaningful_when] if self.meaningful_when is not None else []
        parts += [block.when for block in self.blocks]
        if not parts:
            return frozenset()
        return frozenset().union(*(part.state_names() for part in parts))

    def reasons(self) -> frozenset[str]:
        return frozenset(block.reason for block in self.blocks)


NO_GATES = Gates()


@dataclass(frozen=True)
class StateRule:
    """One arm of a category's state machine. Rules are ordered; the first match wins."""

    name: str
    when: Cond


def resolve_state(rules: tuple[StateRule, ...], ctx: GateContext) -> str | None:
    """The first rule whose condition definitely holds, or ``None``.

    An unknown verdict is not a match: a rule that cannot be evaluated must not claim the
    appliance. ``None`` therefore means "no rule matched", and every gate comparing against
    ``@state`` then evaluates to unknown and falls open — which is the intended behaviour
    for a partial frame that arrived before the first full snapshot.
    """
    for rule in rules:
        if rule.when.holds(ctx) is True:
            return rule.name
    return None
