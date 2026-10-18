"""Property-based tests: invariants that must hold for any shift, not just hand-picked ones."""

from datetime import datetime, timedelta
from decimal import Decimal

from conftest import profile
from hypothesis import given, settings
from hypothesis import strategies as st

from wagecheck.engine import Calculator
from wagecheck.models import Shift
from wagecheck.rules.loader import load_pack

PACK = load_pack("MA000004")
CALC = Calculator(PACK)
BASE = datetime(2026, 10, 5)  # a Monday, after the DST change

starts = st.integers(min_value=0, max_value=7 * 24 * 4 - 1).map(lambda q: BASE + timedelta(minutes=15 * q))
durations = st.integers(min_value=1, max_value=48).map(lambda q: timedelta(minutes=15 * q))
flags = st.booleans()


@settings(max_examples=150, deadline=None)
@given(starts, durations, flags)
def test_casual_never_paid_less_than_part_time(start, dur, sw):
    s = Shift(start=start, end=start + dur)
    assert CALC.calculate(s, profile("casual", sw)).total >= CALC.calculate(s, profile("part_time", sw)).total


@settings(max_examples=150, deadline=None)
@given(starts, durations, flags)
def test_lines_account_for_every_paid_minute(start, dur, sw):
    r = CALC.calculate(Shift(start=start, end=start + dur), profile("full_time", sw))
    assert sum(li.minutes for li in r.lines) == r.paid_minutes == int(dur.total_seconds() // 60)


@settings(max_examples=150, deadline=None)
@given(starts, durations, flags)
def test_working_longer_never_pays_less_for_worked_time(start, dur, sw):
    # Worked time only: the minimum-engagement top-up rate is interpretive (see calculator note),
    # and Hypothesis showed a 2h45 Sunday-night shift topped up at Sunday rates can beat a 3h one
    # that spills into Monday. Payslips round per line, so allow half a cent of drift per line.
    def worked(r):
        return sum((li.amount for li in r.lines if li.band != "min_engagement_topup"), Decimal(0))

    a = CALC.calculate(Shift(start=start, end=start + dur), profile("casual", sw))
    b = CALC.calculate(Shift(start=start, end=start + dur + timedelta(minutes=15)), profile("casual", sw))
    tolerance = Decimal("0.005") * (len(a.lines) + len(b.lines))
    assert worked(b) >= worked(a) - tolerance


@settings(max_examples=150, deadline=None)
@given(starts, durations, flags)
def test_no_minute_paid_below_base_rate(start, dur, sw):
    r = CALC.calculate(Shift(start=start, end=start + dur), profile("full_time", sw))
    base = PACK.classifications["retail_employee_level_1"].hourly
    assert all(li.percent >= 100 and li.rate >= base for li in r.lines)
