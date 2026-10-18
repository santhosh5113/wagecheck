"""Domain models shared across extraction, calculation and detection."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CASUAL = "casual"


class DayType(StrEnum):
    WEEKDAY = "weekday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"
    PUBLIC_HOLIDAY = "public_holiday"


class Break(BaseModel):
    model_config = ConfigDict(frozen=True)
    start: datetime
    end: datetime


class Shift(BaseModel):
    """A worked shift. Naive datetimes are read as local wall-clock time in the
    rule pack's timezone; aware datetimes are respected (handles DST nights)."""

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime
    unpaid_breaks: list[Break] = Field(default_factory=list)
    long_day: bool = False  # the one day per week allowed up to 11 ordinary hours (cl 15.5)

    @model_validator(mode="after")
    def _check(self) -> Shift:
        if self.end <= self.start:
            raise ValueError("shift end must be after start")
        if self.end - self.start > timedelta(hours=24):
            raise ValueError("shift longer than 24 hours")
        for b in self.unpaid_breaks:
            if not (self.start <= b.start < b.end <= self.end):
                raise ValueError("break must sit inside the shift")
        return self


class EmployeeProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    classification: str = "retail_employee_level_1"
    employment_type: EmploymentType = EmploymentType.CASUAL
    shiftworker: bool = False        # specifically employed to do shiftwork (cl 23.1)
    extended_trading: bool = False   # store trades past 9pm weekdays / 6pm weekends (cl 15.2(c))


class LineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    work_date: date
    band: str
    minutes: int
    percent: Decimal
    rate: Decimal
    amount: Decimal
    clauses: list[str]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hours(self) -> Decimal:
        return (Decimal(self.minutes) / 60).quantize(Decimal("0.01"))


class ShiftResult(BaseModel):
    shift: Shift
    profile: EmployeeProfile
    instrument_id: str
    is_shiftwork: bool
    paid_minutes: int
    lines: list[LineItem]
    notes: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> Decimal:
        return sum((li.amount for li in self.lines), Decimal("0.00"))
