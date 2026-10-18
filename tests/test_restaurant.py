"""Restaurant Industry Award [MA000119] from 1 July 2026. Every expected dollar figure below was
returned by the FWC MAPD API on 2026-10-18 (`wagecheck mapd penalties MA000119 --rate-type AD`)."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from wagecheck.engine import Calculator, round_cents
from wagecheck.mapd import MapdRate, cross_check
from wagecheck.models import EmployeeProfile, EmploymentType, Shift
from wagecheck.rules.loader import load_pack

INTRO, L2, L3 = "restaurant_introductory", "restaurant_level_2", "restaurant_level_3"


@pytest.fixture(scope="module")
def pack():
    return load_pack("MA000119", date(2026, 10, 18))


@pytest.fixture
def calc(pack):
    return Calculator(pack, public_holidays=[date(2026, 12, 25)])


def prof(level=INTRO, emp="casual"):
    return EmployeeProfile(classification=level, employment_type=EmploymentType(emp))


def s(a, b):
    return Shift(start=datetime.fromisoformat(a), end=datetime.fromisoformat(b))


def bands(r):
    return [(li.band, li.minutes, str(li.rate), str(li.amount)) for li in r.lines]


def test_pack_matches_live_mapd_rates(pack):
    assert pack.integrity_errors() == []
    live = [MapdRate(n, i + 1, Decimal(v), "2026-07-01", None) for i, (n, v) in enumerate([
        ("Introductory Level", "25.74"), ("Level 1", "26.44"), ("Level 2", "27.08"), ("Level 3", "27.97"),
        ("Level 4", "29.45"), ("Level 5", "31.3"), ("Level 6", "32.13")])]
    assert cross_check(pack, live) == []


# (classification, employment, percent) -> MAPD penalty_calculated_value
@pytest.mark.parametrize(("level", "pct", "dollars"), [
    (INTRO, 125, "32.18"),   # casual ordinary
    (INTRO, 150, "38.61"),   # casual Saturday / casual Sunday Intro-L2 / casual OT first 2h
    (INTRO, 175, "45.05"),   # Saturday OT first 2h
    (INTRO, 250, "64.35"),   # casual public holiday
    (L2, 150, "40.62"),
    (L3, 175, "48.95"),      # casual Sunday Level 3-6
    ("restaurant_level_6", 250, "80.33"),
])
def test_percentages_reproduce_mapd_dollars(pack, level, pct, dollars):
    assert round_cents(pack.classifications[level].hourly * pct / 100) == Decimal(dollars)


def test_casual_sunday_rate_depends_on_level(calc):
    assert calc.calculate(s("2026-10-25T10:00", "2026-10-25T14:00"), prof(L2)).total == Decimal("162.48")
    assert calc.calculate(s("2026-10-25T10:00", "2026-10-25T14:00"), prof(L3)).total == Decimal("195.80")


def test_late_night_flat_amounts(calc):
    r = calc.calculate(s("2026-10-19T21:00", "2026-10-20T01:00"), prof())
    assert bands(r) == [
        ("ordinary", 60, "32.18", "32.18"),
        ("late_night_10pm_to_midnight", 120, "35.13", "70.26"),   # 32.18 + 2.95
        ("late_night_midnight_to_6am", 60, "36.60", "36.60"),     # 32.18 + 4.42
    ]


def test_part_of_an_hour_earns_the_whole_flat_amount(calc):
    r = calc.calculate(s("2026-10-19T20:30", "2026-10-19T22:30"), prof())
    late = r.lines[-1]
    assert (late.band, late.minutes) == ("late_night_10pm_to_midnight", 30)
    assert late.amount == Decimal("16.09") + Decimal("2.95")   # half an hour at 125%, full $2.95


def test_casual_overtime_after_12_hours_has_no_casual_loading(calc):
    r = calc.calculate(s("2026-10-21T07:00", "2026-10-21T20:00"), prof())
    assert bands(r)[-1] == ("overtime_first", 60, "38.61", "38.61")   # 150%, not 175%


def test_full_time_saturday_overtime_starts_at_175(calc):
    r = calc.calculate(s("2026-10-24T07:00", "2026-10-24T20:30"), prof(emp="full_time"))
    assert bands(r)[-1] == ("overtime_saturday_first", 120, "45.05", "90.10")
    assert r.total == round_cents(Decimal("32.18") * Decimal("11.5")) + Decimal("90.10")


def test_casual_minimum_engagement_is_two_hours(calc):
    r = calc.calculate(s("2026-10-20T11:00", "2026-10-20T12:00"), prof())
    assert r.total == Decimal("64.36")   # 2 x 32.18


def test_public_holiday(calc):
    assert calc.calculate(s("2026-12-25T10:00", "2026-12-25T13:00"), prof()).total == Decimal("193.05")
