"""Hand-worked cases. Each expected total is computed from the FWO pay guide rates
(level 1 adult, from 1 July 2026) so a failure means the engine or the pack is wrong."""

from datetime import datetime
from decimal import Decimal

import pytest
from conftest import profile, shift

from wagecheck.models import Break


def D(x: str) -> Decimal:
    return Decimal(x)


def bands(result):
    return [(li.band, li.minutes, str(li.rate)) for li in result.lines]


# ---- shiftwork (night fill) ----

def test_casual_night_fill_weekday(calc):
    r = calc.calculate(shift("2026-10-20T22:00", "2026-10-21T06:00"), profile(shiftworker=True))
    assert r.is_shiftwork
    assert bands(r) == [("shiftwork_weekday", 120, "43.11"), ("shiftwork_weekday", 360, "43.11")]
    assert r.total == D("344.88")


def test_shift_from_friday_into_saturday_splits_at_midnight(calc):
    r = calc.calculate(shift("2026-10-23T22:00", "2026-10-24T06:00"), profile(shiftworker=True))
    assert bands(r) == [("shiftwork_weekday", 120, "43.11"), ("shiftwork_saturday", 360, "48.67")]
    assert r.total == D("86.22") + D("292.02")


def test_shift_from_sunday_into_monday(calc):
    r = calc.calculate(shift("2026-10-25T22:00", "2026-10-26T06:00"), profile(shiftworker=True))
    assert bands(r) == [("shiftwork_sunday", 120, "55.62"), ("shiftwork_weekday", 360, "43.11")]
    assert r.total == D("369.90")


def test_part_time_night_fill_is_130_percent(calc):
    r = calc.calculate(shift("2026-10-20T22:00", "2026-10-21T06:00"), profile("part_time", shiftworker=True))
    assert r.total == D("36.15") * 8


def test_shiftworker_breaks_are_paid(calc):
    s = shift("2026-10-20T22:00", "2026-10-21T06:00",
              unpaid_breaks=[Break(start=datetime(2026, 10, 21, 2), end=datetime(2026, 10, 21, 2, 30))])
    r = calc.calculate(s, profile(shiftworker=True))
    assert r.paid_minutes == 480
    assert any("26" in n for n in r.notes)


def test_same_day_shift_inside_span_is_not_shiftwork(calc):
    # cl 24.3: starts 6pm, ends 9pm Monday, entirely inside the span of hours
    r = calc.calculate(shift("2026-10-19T18:00", "2026-10-19T21:00"), profile(shiftworker=True))
    assert not r.is_shiftwork
    assert bands(r) == [("weekday_evening", 180, "41.72")]


def test_dst_night_pays_real_elapsed_time(calc):
    # Clocks jump 2am -> 3am on Sunday 4 Oct 2026 in Sydney: 7 real hours worked.
    r = calc.calculate(shift("2026-10-03T22:00", "2026-10-04T06:00"), profile(shiftworker=True))
    assert r.paid_minutes == 420
    assert bands(r) == [("shiftwork_saturday", 120, "48.67"), ("shiftwork_sunday", 300, "55.62")]
    assert r.total == D("97.34") + D("278.10")
    assert any("daylight" in n for n in r.notes)


# ---- non-shiftwork penalties, span of hours and overtime ----

def test_casual_saturday_with_unpaid_meal_break(calc):
    s = shift("2026-10-24T09:00", "2026-10-24T17:00",
              unpaid_breaks=[Break(start=datetime(2026, 10, 24, 12), end=datetime(2026, 10, 24, 12, 30))])
    r = calc.calculate(s, profile())
    assert r.paid_minutes == 450
    assert r.total == D("312.90")


def test_weekday_evening_penalty_from_6pm(calc):
    r = calc.calculate(shift("2026-10-19T16:00", "2026-10-19T21:00"), profile())
    assert bands(r) == [("ordinary", 120, "34.76"), ("weekday_evening", 180, "41.72")]
    assert r.total == D("194.68")


def test_work_past_9pm_is_overtime_without_extended_trading(calc):
    r = calc.calculate(shift("2026-10-19T17:00", "2026-10-19T22:00"), profile())
    assert bands(r) == [
        ("ordinary", 60, "34.76"),
        ("weekday_evening", 180, "41.72"),
        ("overtime_first", 60, "48.67"),
    ]
    assert r.total == D("208.59")


def test_extended_trading_moves_span_to_11pm(calc):
    r = calc.calculate(shift("2026-10-19T17:00", "2026-10-19T22:00"), profile(extended=True))
    assert r.total == D("34.76") + D("166.88")


def test_daily_overtime_after_9_ordinary_hours(calc):
    s = shift("2026-10-21T07:00", "2026-10-21T18:00",
              unpaid_breaks=[Break(start=datetime(2026, 10, 21, 12), end=datetime(2026, 10, 21, 13))])
    r = calc.calculate(s, profile())
    assert bands(r)[-1] == ("overtime_first", 60, "48.67")
    assert r.total == D("312.84") + D("48.67")


def test_long_day_allows_11_ordinary_hours(calc):
    s = shift("2026-10-21T07:00", "2026-10-21T18:00", long_day=True,
              unpaid_breaks=[Break(start=datetime(2026, 10, 21, 12), end=datetime(2026, 10, 21, 13))])
    assert calc.calculate(s, profile()).total == D("347.60")


def test_public_holiday_casual(calc):
    r = calc.calculate(shift("2026-12-25T09:00", "2026-12-25T15:00"), profile())
    assert bands(r) == [("public_holiday", 360, "69.53")]
    assert r.total == D("417.18")


def test_casual_minimum_engagement_top_up(calc):
    r = calc.calculate(shift("2026-10-20T10:00", "2026-10-20T12:00"), profile())
    assert bands(r)[-1] == ("min_engagement_topup", 60, "34.76")
    assert r.total == D("104.28")


def test_full_time_has_no_minimum_engagement(calc):
    r = calc.calculate(shift("2026-10-20T10:00", "2026-10-20T12:00"), profile("full_time"))
    assert r.total == D("55.62")


def test_every_line_cites_a_clause(calc):
    r = calc.calculate(shift("2026-10-19T17:00", "2026-10-19T22:00"), profile())
    assert all(li.clauses for li in r.lines)


def test_unknown_classification_rejected(calc):
    with pytest.raises(ValueError):
        calc.calculate(shift("2026-10-20T10:00", "2026-10-20T14:00"), profile(level=9))
