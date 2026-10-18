"""Deterministic entitlement calculator.

Walks a shift one real minute at a time (in UTC, so DST nights are correct), labels
each minute with the band the rule pack says applies, then groups minutes into
payslip-style line items. No LLM is involved anywhere in this module.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from wagecheck.models import (
    DayType,
    EmployeeProfile,
    EmploymentType,
    LineItem,
    Shift,
    ShiftResult,
)
from wagecheck.rules.schema import Rate, RulePack, Span

_CENT = Decimal("0.01")
_MINUTE = timedelta(minutes=1)


def round_cents(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class _Minute:
    local: datetime
    band: str
    rate: Rate
    clauses: tuple[str, ...]


class Calculator:
    def __init__(self, pack: RulePack, public_holidays: Iterable[date] = ()) -> None:
        self.pack = pack
        self.tz = ZoneInfo(pack.timezone)
        self.public_holidays = frozenset(public_holidays)

    # ---------- public API ----------

    def calculate(self, shift: Shift, profile: EmployeeProfile) -> ShiftResult:
        if profile.classification not in self.pack.classifications:
            raise ValueError(f"{profile.classification!r} not in {self.pack.instrument_id}")
        base = self.pack.classifications[profile.classification].hourly

        start, end = self._aware(shift.start), self._aware(shift.end)
        is_shift = self._is_shiftwork(start.astimezone(self.tz), end.astimezone(self.tz), profile)
        notes: list[str] = []
        if is_shift and shift.unpaid_breaks and self.pack.shiftwork is not None:
            notes.append(f"Breaks are paid for shiftworkers (cl {self.pack.shiftwork.paid_breaks_clause}).")
        breaks = [] if is_shift else [(self._aware(b.start), self._aware(b.end)) for b in shift.unpaid_breaks]

        minutes = self._label_minutes(start, end, breaks, is_shift, shift.long_day, profile)
        if not minutes:
            raise ValueError("no paid time in shift")
        real = end.astimezone(UTC) - start.astimezone(UTC)
        wall = end.astimezone(self.tz).replace(tzinfo=None) - start.astimezone(self.tz).replace(tzinfo=None)
        if real != wall:
            notes.append("Shift spans a daylight-saving change; paid on real elapsed time.")

        lines = self._group(minutes, base, profile.employment_type)
        topup = self._min_engagement(len(minutes), minutes[-1], base, profile.employment_type)
        if topup:
            notes.append(
                "Minimum engagement top-up priced at the rate of the last worked minute. The award fixes "
                "the hours, not the rate for unworked time, so treat this line as interpretive."
            )
        lines += topup

        return ShiftResult(
            shift=shift,
            profile=profile,
            instrument_id=self.pack.instrument_id,
            is_shiftwork=is_shift,
            paid_minutes=len(minutes),
            lines=lines,
            notes=notes,
        )

    # ---------- classification helpers ----------

    def _aware(self, dt: datetime) -> datetime:
        return dt.replace(tzinfo=self.tz) if dt.tzinfo is None else dt

    def day_type(self, d: date) -> DayType:
        if d in self.public_holidays:
            return DayType.PUBLIC_HOLIDAY
        return {5: DayType.SATURDAY, 6: DayType.SUNDAY}.get(d.weekday(), DayType.WEEKDAY)

    def _span(self, d: date, extended: bool) -> Span | None:
        s = self.pack.span
        if s is None:
            return None
        span = {5: s.saturday, 6: s.sunday}.get(d.weekday(), s.weekday)
        if extended and span.end < s.extended_trading_end:
            span = Span(start=span.start, end=s.extended_trading_end)
        return span

    def _within_span(self, local: datetime, extended: bool) -> bool:
        span = self._span(local.date(), extended)
        return span is None or span.start <= local.time() < span.end

    def _is_shiftwork(self, ls: datetime, le: datetime, profile: EmployeeProfile) -> bool:
        sw = self.pack.shiftwork
        if sw is None or not profile.shiftworker:  # e.g. retail cl 23.1
            return False
        st = ls.time()
        if not (st >= sw.window_start or st < sw.window_end):  # cl 24.1
            return False
        # cl 24.3: a shift starting and finishing on the same day inside the span is not shiftwork
        if ls.date() == le.date():
            span = self._span(ls.date(), profile.extended_trading)
            if span is not None and span.start <= st and le.time() <= span.end:
                return False
        return True

    def _ordinary_band(self, local: datetime, is_shift: bool, classification: str) -> tuple[str, Rate]:
        dt = self.day_type(local.date())
        sw = self.pack.shiftwork
        if is_shift and sw is not None:
            return {
                DayType.PUBLIC_HOLIDAY: ("shiftwork_public_holiday", sw.public_holiday),
                DayType.SATURDAY: ("shiftwork_saturday", sw.saturday),
                DayType.SUNDAY: ("shiftwork_sunday", sw.sunday),
            }.get(dt, ("shiftwork_weekday", sw.weekday))
        for band in self.pack.time_bands:
            if band.matches(dt.value, local.time(), classification):
                return band.name, band.rate
        return "ordinary", self.pack.ordinary

    def _overtime_band(self, local: datetime, ot_minutes_so_far: int) -> tuple[str, Rate]:
        ot = self.pack.overtime
        dt = self.day_type(local.date())
        if dt is DayType.PUBLIC_HOLIDAY:
            return "overtime_public_holiday", ot.public_holiday
        if dt is DayType.SUNDAY:
            return "overtime_sunday", ot.sunday
        first = ot_minutes_so_far < int(ot.first_hours * 60)
        if dt is DayType.SATURDAY and ot.saturday_first is not None and ot.saturday_after is not None:
            return ("overtime_saturday_first", ot.saturday_first) if first else \
                ("overtime_saturday_after", ot.saturday_after)
        return ("overtime_first", ot.mon_sat_first) if first else ("overtime_after", ot.mon_sat_after)

    def _label_minutes(
        self,
        start: datetime,
        end: datetime,
        breaks: list[tuple[datetime, datetime]],
        is_shift: bool,
        long_day: bool,
        profile: EmployeeProfile,
    ) -> list[_Minute]:
        ot = self.pack.overtime
        daily_max = ot.daily_max_hours
        if profile.employment_type is EmploymentType.CASUAL and ot.casual_daily_max_hours is not None:
            daily_max = ot.casual_daily_max_hours
        threshold = int((max(daily_max, ot.long_day_max_hours) if long_day else daily_max) * 60)
        out: list[_Minute] = []
        ordinary_count = ot_count = 0
        clauses: tuple[str, ...]
        t = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        while t < end_utc:
            if any(bs <= t < be for bs, be in breaks):
                t += _MINUTE
                continue
            local = t.astimezone(self.tz)
            outside_span = not is_shift and not self._within_span(local, profile.extended_trading)
            if outside_span or ordinary_count >= threshold:
                band, rate = self._overtime_band(local, ot_count)
                span_reason = f"{self.pack.span.clause if self.pack.span else ''} span of hours"
                reason = span_reason if outside_span else f"over {threshold // 60}h in a day"
                clauses = (ot.trigger_clause, rate.clause, reason)
                ot_count += 1
            else:
                band, rate = self._ordinary_band(local, is_shift, profile.classification)
                clauses = (rate.clause,)
                if is_shift and self.pack.shiftwork is not None:
                    clauses = (self.pack.shiftwork.definition_clause, rate.clause)
                ordinary_count += 1
            out.append(_Minute(local, band, rate, clauses))
            t += _MINUTE
        return out

    # ---------- money ----------

    @staticmethod
    def _pct(rate: Rate, emp: EmploymentType) -> Decimal:
        return rate.casual if emp is EmploymentType.CASUAL else rate.ft_pt

    def _line(self, d: date, band: str, minutes: int, rate: Rate, clauses: tuple[str, ...], base: Decimal,
              emp: EmploymentType) -> LineItem:
        pct = self._pct(rate, emp)
        hourly = round_cents(base * pct / 100 + rate.flat_per_hour)
        if rate.flat_per_part_hour and rate.flat_per_hour:
            # e.g. "$2.95 per hour or part thereof": 30 minutes earns the whole $2.95
            started_hours = -(-minutes // 60)
            amount = round_cents(round_cents(base * pct / 100) * Decimal(minutes) / 60
                                 + rate.flat_per_hour * started_hours)
        else:
            amount = round_cents(hourly * Decimal(minutes) / 60)
        return LineItem(
            work_date=d,
            band=band,
            minutes=minutes,
            percent=pct,
            rate=hourly,
            amount=amount,
            clauses=list(clauses),
        )

    def _group(self, minutes: list[_Minute], base: Decimal, emp: EmploymentType) -> list[LineItem]:
        lines: list[LineItem] = []
        run_start = 0
        for i in range(1, len(minutes) + 1):
            prev = minutes[i - 1]
            key = (prev.band, prev.local.date())
            if i == len(minutes) or (minutes[i].band, minutes[i].local.date()) != key:
                first = minutes[run_start]
                lines.append(self._line(first.local.date(), first.band, i - run_start, first.rate,
                                        first.clauses, base, emp))
                run_start = i
        return lines

    def _min_engagement(self, paid: int, last: _Minute, base: Decimal, emp: EmploymentType) -> list[LineItem]:
        me = self.pack.min_engagement
        if emp is EmploymentType.CASUAL:
            hours, clause = me.casual_hours, me.casual_clause
        elif emp is EmploymentType.PART_TIME:
            hours, clause = me.part_time_hours, me.part_time_clause
        else:
            return []
        shortfall = int(hours * 60) - paid
        if shortfall <= 0:
            return []
        d = last.local.date()
        return [self._line(d, "min_engagement_topup", shortfall, last.rate, (clause,), base, emp)]
