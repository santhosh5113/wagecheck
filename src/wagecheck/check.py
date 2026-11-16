"""Check one pay period: what the award says you were owed for your shifts vs what you were paid.

This is the v1 path behind the web app. The person types their shifts and their gross pay; the
deterministic calculator prices every shift and the difference is reported. No LLM is involved.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from wagecheck.engine.calculator import Calculator, round_cents
from wagecheck.holidays import nsw_holidays
from wagecheck.models import Break, EmployeeProfile, EmploymentType, Shift, ShiftResult
from wagecheck.rules.loader import load_pack
from wagecheck.rules.schema import RulePack

AWARDS = {
    "MA000004": "General Retail Industry Award (shops, supermarkets)",
    "MA000003": "Fast Food Industry Award (takeaway, quick service)",
    "MA000119": "Restaurant Industry Award (restaurants, cafes)",
    "MA000009": "Hospitality Industry (General) Award (hotels, pubs, bars)",
}
# The packs hold rates from 1 July 2026, and the NSW holiday list runs to June 2027.
SUPPORTED_FROM = date(2026, 7, 1)
SUPPORTED_TO = date(2027, 6, 30)
TOLERANCE = Decimal("1.00")  # rounding noise between a payroll system and this calculator
WEEKLY_ORDINARY_HOURS = Decimal(38)


@dataclass(frozen=True)
class ShiftInput:
    day: date
    start: time
    end: time
    break_minutes: int = 0


@dataclass
class PeriodCheck:
    award_id: str
    award_title: str
    classification: str
    hourly_base: Decimal
    results: list[ShiftResult]
    owed: Decimal
    paid: Decimal
    warnings: list[str] = field(default_factory=list)
    known_gaps: list[str] = field(default_factory=list)
    award_url: str | None = None

    @property
    def difference(self) -> Decimal:
        """Positive means paid less than the award minimum."""
        return self.owed - self.paid

    @property
    def verdict(self) -> str:
        if self.difference > TOLERANCE:
            return "underpaid"
        if self.difference < -TOLERANCE:
            return "paid_above"
        return "matches"


def parse_time(text: str) -> time:
    """Read a clock time the way people type it: 22:00, 2200, 22.00, 22, 10pm, 10:30 pm, 9am."""
    s = str(text).strip().lower().replace(" ", "").replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?(am|pm)?", s)
    if not m:
        raise ValueError(f"can't read the time {text!r}: type it like 22:00 or 10pm")
    hour, minute, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if suffix:
        if not 1 <= hour <= 12:
            raise ValueError(f"can't read the time {text!r}")
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    if hour == 24 and minute == 0:
        hour = 0  # 24:00 means midnight
    if hour > 23 or minute > 59:
        raise ValueError(f"can't read the time {text!r}")
    return time(hour, minute)


def classification_label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def to_shift(s: ShiftInput) -> Shift:
    """A finish at or before the start means the shift ran past midnight. The unpaid break is placed in
    the middle of the shift, because the band it falls in can change the amount."""
    start = datetime.combine(s.day, s.start)
    end = datetime.combine(s.day, s.end)
    if end <= start:
        end += timedelta(days=1)
    breaks = []
    if s.break_minutes:
        if timedelta(minutes=s.break_minutes) >= end - start:
            raise ValueError(f"{s.day}: the break is as long as the shift")
        mid = start + (end - start - timedelta(minutes=s.break_minutes)) / 2
        mid = mid.replace(second=0, microsecond=0)
        breaks = [Break(start=mid, end=mid + timedelta(minutes=s.break_minutes))]
    return Shift(start=start, end=end, unpaid_breaks=breaks)


def _award_url(pack: RulePack) -> str | None:
    return next((s.url for s in pack.sources if "awards.fairwork.gov.au" in s.url), None)


def check_period(award_id: str, classification: str, employment_type: EmploymentType | str,
                 shifts: list[ShiftInput], paid_gross: Decimal, shiftworker: bool = False,
                 extended_trading: bool = False) -> PeriodCheck:
    if award_id not in AWARDS:
        raise ValueError(f"{award_id} is not supported yet")
    if not shifts:
        raise ValueError("add at least one shift")
    for s in shifts:
        if not SUPPORTED_FROM <= s.day <= SUPPORTED_TO:
            raise ValueError(f"{s.day}: only shifts from {SUPPORTED_FROM:%d %b %Y} to {SUPPORTED_TO:%d %b %Y} "
                             "can be checked (the 2026-27 award rates)")
    pack = load_pack(award_id, on=min(s.day for s in shifts))
    if classification not in pack.classifications:
        raise ValueError(f"{classification} is not a classification in {award_id}")
    emp = EmploymentType(employment_type)
    profile = EmployeeProfile(classification=classification, employment_type=emp, shiftworker=shiftworker,
                              extended_trading=extended_trading)
    calc = Calculator(pack, nsw_holidays())
    results = [calc.calculate(to_shift(s), profile) for s in sorted(shifts, key=lambda s: (s.day, s.start))]
    owed = round_cents(sum((r.total for r in results), Decimal(0)))

    warnings = []
    if emp is not EmploymentType.CASUAL:
        weeks: dict[date, Decimal] = defaultdict(Decimal)
        for r in results:
            weeks[r.shift.start.date() - timedelta(days=r.shift.start.weekday())] += Decimal(r.paid_minutes) / 60
        for monday, hours in sorted(weeks.items()):
            if hours > WEEKLY_ORDINARY_HOURS:
                warnings.append(f"Week of {monday:%d %b}: {hours:.2f} hours is over 38. Weekly overtime isn't "
                                "checked yet, so the amount owed may be higher than shown.")
    if pack.shiftwork is not None and not shiftworker:
        outside = [r for r in results if any("span of hours" in c for li in r.lines for c in li.clauses)]
        if outside:
            days = ", ".join(f"{r.shift.start:%a %d %b}" for r in outside)
            warnings.append(f"{days}: time outside ordinary hours is priced as overtime. If you were hired "
                            "specifically to work nights (e.g. night fill), tick the night-shift box: shiftwork "
                            "rates are lower than overtime, so this result may overstate what you're owed.")
    for r in results:
        warnings += [f"{r.shift.start:%a %d %b}: {n}" for n in r.notes]
    return PeriodCheck(award_id=award_id, award_title=pack.title, classification=classification,
                       hourly_base=pack.classifications[classification].hourly, results=results, owed=owed,
                       paid=round_cents(Decimal(paid_gross)), warnings=warnings, known_gaps=list(pack.known_gaps),
                       award_url=_award_url(pack))
