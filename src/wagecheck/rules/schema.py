"""Typed schema for an instrument rule pack (modern award or enterprise agreement).

Rule packs are data, not code: the engine never hard-codes a percentage or a dollar
figure. Every rate carries the clause it came from so reports can cite it.
"""

from __future__ import annotations

from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Source(_Frozen):
    title: str
    url: str


class Rate(_Frozen):
    """Percentage of the minimum hourly rate, for permanent and casual employees, plus an
    optional flat dollar amount per hour (e.g. a late-night loading paid as $/hour)."""

    ft_pt: Decimal
    casual: Decimal  # inclusive of casual loading
    clause: str
    flat_per_hour: Decimal = Decimal(0)
    # "per hour or part thereof": any part of an hour attracts the whole flat amount
    flat_per_part_hour: bool = False
    # True where the award deliberately sets a casual % that isn't ft_pt + loading
    loading_exception: bool = False


DayName = Literal["weekday", "saturday", "sunday", "public_holiday"]


class TimeBand(_Frozen):
    """One row of a penalty table. Bands are checked in order; the first match wins, so list
    public holidays first, then weekend days, then weekday time windows."""

    name: str
    days: list[DayName] = Field(min_length=1)
    start: time | None = None  # inclusive; None = from midnight
    end: time | None = None    # exclusive; None or 00:00 = until midnight
    levels: list[str] | None = None  # only these classifications; None = all
    rate: Rate

    def matches(self, day: str, t: time, classification: str) -> bool:
        if day not in self.days:
            return False
        if self.levels is not None and classification not in self.levels:
            return False
        if self.start is not None and t < self.start:
            return False
        return self.end is None or self.end == time(0) or t < self.end


class Classification(_Frozen):
    weekly: Decimal | None = None  # omitted where only the hourly rate is published (e.g. from MAPD)
    hourly: Decimal
    clause: str
    mapd_level: int | None = None  # classification_level in MAPD records, for cross-checks
    mapd_name: str | None = None   # MAPD `classification` text; wins over mapd_level when set
    mapd_parent: str | None = None  # MAPD `parent_classification_name`, where one name spans streams
    mapd_fixed_id: int | None = None  # MAPD `classification_fixed_id`; the most exact match when set


class Span(_Frozen):
    start: time
    end: time


class SpanOfHours(_Frozen):
    clause: str
    weekday: Span
    saturday: Span
    sunday: Span
    extended_trading_end: time
    extended_trading_clause: str


class Shiftwork(_Frozen):
    definition_clause: str
    same_day_exclusion_clause: str
    window_start: time
    window_end: time
    weekday: Rate
    saturday: Rate
    sunday: Rate
    public_holiday: Rate
    paid_breaks_clause: str


class Overtime(_Frozen):
    trigger_clause: str
    daily_max_hours: Decimal
    long_day_max_hours: Decimal
    casual_daily_max_hours: Decimal | None = None  # where casuals have a different daily limit
    first_hours: Decimal
    mon_sat_first: Rate                # Monday to Friday, and Saturday unless saturday_* is set
    mon_sat_after: Rate
    saturday_first: Rate | None = None
    saturday_after: Rate | None = None
    sunday: Rate
    public_holiday: Rate


class MinEngagement(_Frozen):
    casual_hours: Decimal
    casual_clause: str
    part_time_hours: Decimal
    part_time_clause: str


class RulePack(_Frozen):
    instrument_id: str
    instrument_type: Literal["modern_award", "enterprise_agreement"]
    title: str
    effective_from: date
    verified_on: date
    timezone: str
    sources: list[Source] = Field(min_length=1)
    classifications: dict[str, Classification]
    casual_loading_pct: Decimal
    casual_loading_clause: str
    min_engagement: MinEngagement
    ordinary: Rate
    time_bands: list[TimeBand]
    span: SpanOfHours | None = None        # awards without a span of ordinary hours omit this
    shiftwork: Shiftwork | None = None     # awards without a separate shiftwork regime omit this
    overtime: Overtime
    known_gaps: list[str] = Field(default_factory=list)

    def integrity_errors(self) -> list[str]:
        """Cheap self-consistency checks that catch transcription typos.

        For modern awards the FWC publishes weekly rates and derives the hourly
        rate as weekly / 38, rounded to the cent.
        """
        errors: list[str] = []
        if self.instrument_type == "modern_award":
            for name, c in self.classifications.items():
                if c.weekly is None:
                    continue
                derived = (c.weekly / Decimal(38)).quantize(Decimal("0.01"), ROUND_HALF_UP)
                if derived != c.hourly:
                    errors.append(f"{name}: hourly {c.hourly} != weekly/38 {derived}")
        for band in [TimeBand(name="ordinary", days=["weekday"], rate=self.ordinary), *self.time_bands]:
            r = band.rate
            if not r.loading_exception and r.casual - r.ft_pt != self.casual_loading_pct:
                errors.append(f"time_bands.{band.name}: casual {r.casual} vs ft_pt {r.ft_pt}")
            for lvl in band.levels or []:
                if lvl not in self.classifications:
                    errors.append(f"time_bands.{band.name}: unknown level {lvl}")
        return errors
