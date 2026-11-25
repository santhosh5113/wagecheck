"""Coles Retail Enterprise Agreement 2024 pack. Hand-worked cases; every expected figure is base rate x the
clause percentage, rounded to the cent per hour, then x hours. Shifts are made up (no real payslip data)."""

from collections import Counter
from datetime import date, time
from decimal import Decimal

import pytest
import yaml

from wagecheck.check import ShiftInput, check_period
from wagecheck.rules.loader import load_pack

AG = "coles-retail-ea-2024"
L1 = "coles_level_1"


def run(day: str, start: str, end: str, emp: str = "part_time", shiftworker: bool = False, brk: int = 0):
    s = ShiftInput(date.fromisoformat(day), time.fromisoformat(start), time.fromisoformat(end), brk)
    return check_period(AG, L1, emp, [s], Decimal(0), shiftworker=shiftworker)


def split(c) -> dict[int, float]:
    out: Counter[int] = Counter()
    for r in c.results:
        for li in r.lines:
            out[int(li.percent)] += li.minutes / 60
    return dict(sorted(out.items()))


@pytest.fixture(scope="module")
def pack():
    return load_pack(AG, on=date(2026, 11, 1))


def test_pack_is_consistent(pack):
    assert pack.instrument_type == "enterprise_agreement"
    assert pack.integrity_errors() == []
    assert len(pack.classifications) == 6  # the agreement has six levels


@pytest.mark.parametrize("level, agreement_rate", [(1, "25.27"), (2, "25.88"), (3, "26.28"), (4, "26.80"),
                                                   (5, "27.89"), (6, "28.31")])
def test_base_rates_follow_the_cl_3_4_1_indexation(pack, level, agreement_rate):
    # x1.0375 (July 2024), x1.035 (July 2025), x1.0475 (July 2026); Level 1 gives $28.42417, the rate
    # printed on a real Nov 2026 Coles payslip
    indexed = (Decimal(agreement_rate) * Decimal("1.0375") * Decimal("1.035") * Decimal("1.0475")
               ).quantize(Decimal("0.00001"))
    assert pack.classifications[f"coles_level_{level}"].hourly == indexed


def test_weeknight_after_11pm_is_overtime_150_then_200_from_2am():
    # Wed 21:30 - Thu 03:00, part-time, not a shiftworker:
    # 21:30-23:00 evening 125% (cl 3.3.1), 23:00-02:00 overtime 150% (cl 4.2.3, 4.9.2(c)), 02:00-03:00 200%
    c = run("2026-11-18", "21:30", "03:00")
    assert split(c) == {125: 1.5, 150: 3.0, 200: 1.0}
    assert c.owed == Decimal("53.30") + Decimal("42.64") * 3 + Decimal("56.85")


def test_three_hour_count_runs_across_midnight_not_reset():
    # 22:30 - 04:00: overtime 23:00-02:00 at 150% even though midnight falls inside it
    c = run("2026-10-26", "22:30", "04:00")
    assert split(c) == {125: 0.5, 150: 3.0, 200: 2.0}


def test_sunday_night_overtime_counts_toward_the_first_three_hours():
    # Sun 22:00-23:00 Sunday 150%; 23:00-00:00 Sunday overtime 200%; that hour uses up one of the three,
    # so Monday 00:00-02:00 is 150% and 02:00-03:30 is 200% (matches how Coles payroll pays it)
    c = run("2026-11-01", "22:00", "03:30")
    assert split(c) == {150: 3.0, 200: 2.5}
    assert c.owed == Decimal("42.64") + Decimal("56.85") + Decimal("85.28") + Decimal("85.28")


def test_unpaid_break_is_deducted_for_non_shiftworkers():
    assert sum(split(run("2026-11-02", "22:00", "04:30", brk=30)).values()) == 6.0


def test_shiftworker_is_paid_130_for_the_whole_shift_including_breaks():
    # cl 4.5.4(b) 130% Sunday midnight to Friday midnight; cl 4.5.5 breaks are paid
    c = run("2026-11-18", "21:30", "03:00", shiftworker=True, brk=30)
    assert split(c) == {130: 5.5}
    assert c.owed == Decimal("92.38") + Decimal("110.85")  # 2.5h and 3h at $36.95
    assert not any("tick the shiftworker box" in w for w in c.warnings)


def test_shiftworker_friday_night_into_saturday():
    # Fri 22:00-00:00 at 130%, Sat 00:00-04:00 at 150% (cl 4.5.4(c))
    c = run("2026-11-20", "22:00", "04:00", shiftworker=True)
    assert split(c) == {130: 2.0, 150: 4.0}
    assert c.owed == Decimal("73.90") + Decimal("170.56")


def test_non_shiftworker_night_gets_the_shiftworker_warning():
    c = run("2026-11-18", "21:30", "03:00")
    assert any("tick the shiftworker box" in w for w in c.warnings)


def test_casual_rates_include_the_loading():
    # Wed 22:00-02:00 casual: 22:00-23:00 evening 150%, overtime 175% for 3 hours (cl 4.9.3(b))
    c = run("2026-11-18", "22:00", "02:00", emp="casual")
    assert split(c) == {150: 1.0, 175: 3.0}
    assert c.owed == Decimal("42.64") + Decimal("49.74") * 3


@pytest.mark.parametrize("day, start, end, expected", [
    ("2026-11-21", "09:00", "17:00", {125: 8.0}),   # Saturday 7am-11pm: +25% (cl 3.3.1)
    ("2026-11-22", "10:00", "16:00", {150: 6.0}),   # Sunday 9am-11pm: +50%
    ("2026-11-19", "09:00", "17:00", {100: 8.0}),   # weekday daytime: base rate
])
def test_daytime_penalties(day, start, end, expected):
    assert split(run(day, start, end)) == expected


def test_registry_entry_points_at_this_pack():
    from importlib import resources

    reg = yaml.safe_load(resources.files("wagecheck.coverage").joinpath("data/agreements.yaml").read_text())
    assert any(a["id"] == AG for a in reg["agreements"])
    assert load_pack(AG, on=date(2026, 11, 1)).instrument_id == AG
