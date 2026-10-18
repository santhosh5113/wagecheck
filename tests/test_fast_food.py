"""Fast Food Industry Award [MA000003] from 1 July 2026. Expected dollar figures are the
published 2026-27 level 1 rates: $27.81 base, $34.76 casual, Sat/Sun(L1) casual $41.72,
public holiday casual $69.53, 10pm-midnight casual $37.54, midnight-6am casual $38.93."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from wagecheck.engine import Calculator, round_cents
from wagecheck.models import Break, EmployeeProfile, EmploymentType, Shift
from wagecheck.rules.loader import load_pack

L1 = "fast_food_employee_level_1"
L2 = "fast_food_employee_level_2"


@pytest.fixture(scope="module")
def pack():
    return load_pack("MA000003", date(2026, 10, 18))


@pytest.fixture
def calc(pack):
    return Calculator(pack, public_holidays=[date(2026, 12, 25)])


def prof(level=L1, emp="casual", shiftworker=False):
    return EmployeeProfile(classification=level, employment_type=EmploymentType(emp), shiftworker=shiftworker)


def s(a, b, **kw):
    return Shift(start=datetime.fromisoformat(a), end=datetime.fromisoformat(b), **kw)


def bands(r):
    return [(li.band, li.minutes, str(li.rate)) for li in r.lines]


def test_pack_is_consistent(pack):
    assert pack.integrity_errors() == []
    assert pack.span is None and pack.shiftwork is None


@pytest.mark.parametrize(("pct", "dollars"), [
    (125, "34.76"), (135, "37.54"), (140, "38.93"), (150, "41.72"), (250, "69.53"), (110, "30.59"),
    (115, "31.98"),
])
def test_level1_rates_match_published_figures(pack, pct, dollars):
    assert round_cents(pack.classifications[L1].hourly * pct / 100) == Decimal(dollars)


def test_sunday_rate_depends_on_level(calc):
    l1 = calc.calculate(s("2026-10-25T10:00", "2026-10-25T14:00"), prof(L1))
    l2 = calc.calculate(s("2026-10-25T10:00", "2026-10-25T14:00"), prof(L2))
    assert bands(l1) == [("sunday", 240, "41.72")]
    assert bands(l2) == [("sunday", 240, "51.54")]  # 29.45 x 175%


def test_late_night_bands_across_midnight(calc):
    r = calc.calculate(s("2026-10-19T20:00", "2026-10-20T02:00"), prof())
    assert bands(r) == [
        ("ordinary", 120, "34.76"),
        ("late_night_10pm_to_midnight", 120, "37.54"),
        ("late_night_midnight_to_6am", 120, "38.93"),
    ]
    assert r.total == Decimal("222.46")


def test_friday_night_into_saturday_takes_saturday_rate(calc):
    r = calc.calculate(s("2026-10-23T22:00", "2026-10-24T02:00"), prof())
    assert bands(r) == [("late_night_10pm_to_midnight", 120, "37.54"), ("saturday", 120, "41.72")]


def test_no_shiftwork_regime_even_if_flagged(calc):
    r = calc.calculate(s("2026-10-20T22:00", "2026-10-21T02:00"), prof(shiftworker=True))
    assert not r.is_shiftwork
    assert r.total == Decimal("75.08") + Decimal("77.86")


def test_no_span_of_hours_overtime(calc):
    # 9pm-11pm is outside retail's span but ordinary time here
    r = calc.calculate(s("2026-10-21T19:00", "2026-10-21T23:00"), prof())
    assert not any(li.band.startswith("overtime") for li in r.lines)


def test_overtime_after_11_hours_first_two_hours_then_225(calc):
    brk = [Break(start=datetime(2026, 10, 21, 12), end=datetime(2026, 10, 21, 12, 30))]
    r = calc.calculate(s("2026-10-21T07:00", "2026-10-21T21:30", unpaid_breaks=brk), prof())
    assert bands(r)[-2:] == [("overtime_first", 120, "48.67"), ("overtime_after", 60, "62.57")]
    assert r.total == Decimal("382.36") + Decimal("97.34") + Decimal("62.57")


def test_public_holiday(calc):
    assert calc.calculate(s("2026-12-25T10:00", "2026-12-25T14:00"), prof()).total == Decimal("278.12")


def test_minimum_engagement(calc):
    r = calc.calculate(s("2026-10-20T11:00", "2026-10-20T13:00"), prof())
    assert r.total == Decimal("104.28")


def test_flat_dollar_loading_is_added_per_hour(pack):
    # Restaurant/Hospitality style: % of base plus a fixed $/hour (values here are illustrative).
    from wagecheck.rules.schema import Rate, TimeBand

    band = TimeBand(name="late_night_flat", days=["weekday"], start="22:00",
                    rate=Rate(ft_pt=100, casual=125, flat_per_hour=Decimal("2.81"), clause="test"))
    flat_pack = pack.model_copy(update={"time_bands": [band]})
    r = Calculator(flat_pack).calculate(s("2026-10-20T22:00", "2026-10-20T23:30"), prof(emp="full_time"))
    assert bands(r) == [("late_night_flat", 90, "30.62")]   # 27.81 + 2.81
    assert r.total == Decimal("45.93")


def test_integrity_catches_unknown_level(pack):
    bad = pack.time_bands[1].model_copy(update={"levels": ["fast_food_employee_level_9"]})
    broken = pack.model_copy(update={"time_bands": [bad]})
    assert any("unknown level" in e for e in broken.integrity_errors())
