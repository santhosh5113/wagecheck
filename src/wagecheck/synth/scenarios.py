"""Realistic work patterns for each supported award, and roster generation.

A scenario is a kind of job (e.g. retail night fill) with the shift shapes it typically has. Rosters
are drawn from those shapes; extra short or long shifts are added when an injected error needs one
(minimum engagement, overtime).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from wagecheck.models import Break, EmploymentType, Shift


@dataclass(frozen=True)
class ShiftShape:
    start: time
    hours: float
    days: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)  # Monday = 0


@dataclass(frozen=True)
class Scenario:
    key: str
    award: str
    job_title: str
    workplace: str
    classifications: tuple[str, ...]
    employment: tuple[EmploymentType, ...]
    shapes: tuple[ShiftShape, ...]
    shifts_per_week: tuple[int, int]
    shiftworker: bool = False
    extended_trading: bool = False
    employer_names: tuple[str, ...] = field(default_factory=tuple)


C, P = EmploymentType.CASUAL, EmploymentType.PART_TIME
WEEKEND = (5, 6)

SCENARIOS: tuple[Scenario, ...] = (
    Scenario("retail_night_fill", "MA000004", "Night Fill Team Member", "supermarket",
             ("retail_employee_level_1",), (C, C, P),
             (ShiftShape(time(22), 8), ShiftShape(time(23), 7), ShiftShape(time(22), 6)), (3, 5),
             shiftworker=True,
             employer_names=("Harbour Fresh Grocers", "Westgate Food Market", "Northside Fresh Foods")),
    Scenario("retail_day", "MA000004", "Retail Assistant", "clothing store",
             ("retail_employee_level_1", "retail_employee_level_2", "retail_employee_level_3"), (C, C, P),
             (ShiftShape(time(9), 8), ShiftShape(time(16), 5, (0, 1, 2, 3, 4)), ShiftShape(time(10), 6, WEEKEND),
              ShiftShape(time(12), 6)), (2, 4), extended_trading=False,
             employer_names=("Thread Lane Apparel", "Corner Hardware Co", "Bright Home Goods")),
    Scenario("fast_food", "MA000003", "Crew Member", "takeaway",
             ("fast_food_employee_level_1", "fast_food_employee_level_2"), (C,),
             (ShiftShape(time(17), 6), ShiftShape(time(18), 7), ShiftShape(time(11), 6, WEEKEND),
              ShiftShape(time(20), 5)), (3, 5),
             employer_names=("Grill Stack Burgers", "Slice Express Pizza", "Kebab Corner")),
    Scenario("restaurant", "MA000119", "Barista", "cafe",
             ("restaurant_introductory", "restaurant_level_1", "restaurant_level_2", "restaurant_level_3"), (C,),
             (ShiftShape(time(7), 8), ShiftShape(time(17), 6), ShiftShape(time(8), 7, WEEKEND),
              ShiftShape(time(18), 5)), (3, 5),
             employer_names=("Little Bean Cafe", "Olive and Thyme", "Spice Route Dining")),
    Scenario("hospitality", "MA000009", "Bar Attendant", "pub",
             ("hospitality_introductory", "hospitality_level_1", "hospitality_level_2", "hospitality_level_3"),
             (C, C, P),
             (ShiftShape(time(18), 8), ShiftShape(time(16), 8), ShiftShape(time(12), 7, WEEKEND),
              ShiftShape(time(19), 6)), (3, 5),
             employer_names=("The Royal Oak Hotel", "Harbourview Tavern", "Skyline Bar")),
)

BY_KEY = {s.key: s for s in SCENARIOS}


def _break(start: datetime, hours: float, shiftworker: bool) -> list[Break]:
    """A 30-minute unpaid meal break near the middle of shifts over 5 hours (non-shiftworkers)."""
    if shiftworker or hours <= 5:
        return []
    mid = start + timedelta(hours=hours / 2)
    mid = mid.replace(minute=0 if mid.minute < 30 else 30, second=0, microsecond=0)
    return [Break(start=mid, end=mid + timedelta(minutes=30))]


def make_shift(day: date, start: time, hours: float, shiftworker: bool) -> Shift:
    s = datetime.combine(day, start)
    brk = _break(s, hours, shiftworker)
    paid = timedelta(hours=hours) + sum((b.end - b.start for b in brk), timedelta())
    return Shift(start=s, end=s + paid, unpaid_breaks=brk)


def roster(scenario: Scenario, period_start: date, days: int, rng: random.Random,
           need_short: bool = False, need_long: bool = False) -> list[Shift]:
    """Shifts within [period_start, period_start + days). At most one shift starts per day."""
    shifts: list[Shift] = []
    for week in range(days // 7):
        week_start = period_start + timedelta(days=7 * week)
        n = rng.randint(*scenario.shifts_per_week)
        for d in sorted(rng.sample(range(7), n)):
            day = week_start + timedelta(days=d)
            options = [sh for sh in scenario.shapes if day.weekday() in sh.days]
            sh = rng.choice(options)
            shifts.append(make_shift(day, sh.start, sh.hours, scenario.shiftworker))
    used = {s.start.date() for s in shifts}
    free = [period_start + timedelta(days=i) for i in range(days)
            if period_start + timedelta(days=i) not in used]
    rng.shuffle(free)
    if need_short and free:
        # a short daytime shift, under every award's minimum engagement
        shifts.append(make_shift(free.pop(), time(11), 1.5, False))
    if need_long and free:
        # a long double shift that runs past the daily overtime limit
        shifts.append(make_shift(free.pop(), time(7), 13.0, False))
    return sorted(shifts, key=lambda s: s.start)
