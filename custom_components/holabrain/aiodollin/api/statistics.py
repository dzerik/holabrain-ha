"""Water and energy consumption, as the cloud aggregates it.

The appliance itself cannot answer "how much energy did you use last August" — the cloud
books each finished cycle against the calendar and serves the totals back. That makes this
a genuinely separate source from the status snapshot, with three useful properties:

* the figures are already in kilowatt-hours and litres, so nothing has to be scaled or
  guessed at;
* they survive re-pairing, because they belong to the appliance's record rather than to the
  current binding; and
* they only change when a cycle finishes, so one request per wash keeps them current.

That last point matters more than it sounds: every request competes for the account's single
session. Daily granularity means there is nothing to gain from asking more often.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..auth.manager import AuthManager

#: Report windows the cloud offers. ``day`` is the last seven days, not today alone.
PERIOD_DAY = "day"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"

_PATH = "/v1/statistics/data/report/{period}"


@dataclass(frozen=True)
class ConsumptionPoint:
    """One bucket of the report: a date (``YYYY-MM-DD`` or ``YYYY-MM``) and its value."""

    date: str
    energy_kwh: float | None = None
    water_litres: float | None = None


@dataclass(frozen=True)
class ConsumptionReport:
    """Totals for one window, plus the buckets that make them up."""

    period: str
    total_energy_kwh: float | None = None
    total_water_litres: float | None = None
    points: tuple[ConsumptionPoint, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, period: str, payload: Any) -> ConsumptionReport:
        if not isinstance(payload, dict):
            return cls(period=period)
        energy = {
            point.get("date"): _number(point.get("value"))
            for point in payload.get("energyDetail") or []
            if isinstance(point, dict)
        }
        water = {
            point.get("date"): _number(point.get("value"))
            for point in payload.get("waterDetail") or []
            if isinstance(point, dict)
        }
        # The two series are reported separately but cover the same buckets; a model that
        # meters only one of them still produces a usable report for the other.
        dates = list(dict.fromkeys([*energy, *water]))
        return cls(
            period=period,
            total_energy_kwh=_number(payload.get("totalEnergy")),
            total_water_litres=_number(payload.get("totalWater")),
            points=tuple(
                ConsumptionPoint(
                    date=str(date),
                    energy_kwh=energy.get(date),
                    water_litres=water.get(date),
                )
                for date in dates
                if date is not None
            ),
        )

    def value_on(self, date: str) -> ConsumptionPoint | None:
        """The bucket for ``date``, if the report covers it."""
        for point in self.points:
            if point.date == date:
                return point
        return None


def _number(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class StatisticsApi:
    """Consumption reports for one appliance."""

    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth

    async def async_report(self, thing_code: str, period: str) -> ConsumptionReport:
        """Fetch one window. ``period`` is ``day``, ``month`` or ``year``."""
        response = await self._auth.oem(
            _PATH.format(period=period), {"thingCode": thing_code}
        )
        return ConsumptionReport.from_dict(period, response.get("data"))
