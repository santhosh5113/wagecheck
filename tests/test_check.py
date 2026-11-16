from datetime import date, time
from decimal import Decimal

import pytest

from wagecheck.check import ShiftInput, check_period, parse_time, rows_to_shifts, to_shift


def night(day: int) -> ShiftInput:
    return ShiftInput(date(2026, 10, day), time(22, 0), time(6, 0))


def test_night_fill_matches_hand_worked_calculator_case():
    # Same shift as test_calculator.test_casual_night_fill_weekday: 8 hours at $43.11 = $344.88
    c = check_period("MA000004", "retail_employee_level_1", "casual", [night(20)], Decimal("344.88"),
                     shiftworker=True)
    assert c.owed == Decimal("344.88") and c.verdict == "matches"


def test_underpaid_and_paid_above():
    shifts = [night(20), ShiftInput(date(2026, 10, 23), time(22, 0), time(6, 0))]  # Fri into Sat
    c = check_period("MA000004", "retail_employee_level_1", "casual", shifts, Decimal("600"), shiftworker=True)
    assert c.owed == Decimal("344.88") + Decimal("86.22") + Decimal("292.02")
    assert c.verdict == "underpaid" and c.difference == c.owed - Decimal("600")
    assert check_period("MA000004", "retail_employee_level_1", "casual", shifts, Decimal("900"),
                        shiftworker=True).verdict == "paid_above"


def test_finish_before_start_runs_past_midnight_and_break_sits_mid_shift():
    s = to_shift(ShiftInput(date(2026, 10, 20), time(22, 0), time(6, 0), break_minutes=30))
    assert s.end.day == 21
    b = s.unpaid_breaks[0]
    assert (b.start.hour, b.start.minute, b.end.hour, b.end.minute) == (1, 45, 2, 15)


def test_dates_outside_the_rate_year_are_refused():
    with pytest.raises(ValueError, match="only shifts from"):
        check_period("MA000004", "retail_employee_level_1", "casual",
                     [ShiftInput(date(2026, 6, 30), time(9), time(17))], Decimal(0))


def test_unknown_classification_is_refused():
    with pytest.raises(ValueError, match="not a classification"):
        check_period("MA000003", "retail_employee_level_1", "casual",
                     [ShiftInput(date(2026, 10, 20), time(9), time(17))], Decimal(0))


def test_part_time_over_38_hours_a_week_is_flagged():
    week = [ShiftInput(date(2026, 10, d), time(8), time(17)) for d in range(19, 24)]  # Mon-Fri, 9 hours each
    c = check_period("MA000003", "fast_food_employee_level_1", "part_time", week, Decimal(0))
    assert any("over 38" in w for w in c.warnings)
    assert c.known_gaps and c.award_url


def test_night_shift_without_shiftwork_box_warns_about_overtime():
    plain = check_period("MA000004", "retail_employee_level_1", "casual", [night(20)], Decimal(0))
    assert any("tick the night-shift box" in w for w in plain.warnings)
    shiftwork = check_period("MA000004", "retail_employee_level_1", "casual", [night(20)], Decimal(0),
                             shiftworker=True)
    assert not any("tick the night-shift box" in w for w in shiftwork.warnings)
    assert shiftwork.owed < plain.owed


def test_day_shifts_get_no_overtime_warning():
    day = ShiftInput(date(2026, 10, 20), time(9), time(17), break_minutes=30)
    c = check_period("MA000004", "retail_employee_level_1", "casual", [day], Decimal(0))
    assert not any("night-shift box" in w for w in c.warnings)


@pytest.mark.parametrize("typed, expected", [
    ("22:00", time(22)), ("2200", time(22)), ("22", time(22)), ("22.30", time(22, 30)), ("10pm", time(22)),
    ("10:30 PM", time(22, 30)), ("6am", time(6)), ("12am", time(0)), ("12pm", time(12)), ("24:00", time(0)),
    (" 9:05 ", time(9, 5)),
])
def test_times_are_read_the_way_people_type_them(typed, expected):
    assert parse_time(typed) == expected


@pytest.mark.parametrize("typed", ["25:00", "13pm", "9:75", "noon", ""])
def test_unreadable_times_are_refused(typed):
    with pytest.raises(ValueError, match="can't read the time"):
        parse_time(typed)


def test_table_rows_become_shifts_and_empty_rows_are_skipped():
    rows = [{"date": date(2026, 10, 20), "start": "22:00", "finish": "6am", "unpaid break (min)": 30},
            {"date": None, "start": None, "finish": None, "unpaid break (min)": None},
            {"date": "2026-10-24 00:00:00", "start": "0900", "finish": "17", "unpaid break (min)": float("nan")}]
    assert rows_to_shifts(rows) == [ShiftInput(date(2026, 10, 20), time(22), time(6), 30),
                                    ShiftInput(date(2026, 10, 24), time(9), time(17), 0)]


def test_half_filled_row_is_named():
    rows = [{"date": date(2026, 10, 20), "start": "22:00", "finish": "06:00", "unpaid break (min)": 30},
            {"date": date(2026, 10, 24), "start": "", "finish": None, "unpaid break (min)": None}]
    with pytest.raises(ValueError, match="Shift row 2 is missing"):
        rows_to_shifts(rows)
    rows[1].update(start="9", finish="25:00")
    with pytest.raises(ValueError, match="Shift row 2: can't read the time '25:00'"):
        rows_to_shifts(rows)
