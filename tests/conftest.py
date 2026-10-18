from datetime import date, datetime

import pytest

from wagecheck.engine import Calculator
from wagecheck.models import EmployeeProfile, EmploymentType, Shift
from wagecheck.rules.loader import load_pack


@pytest.fixture(scope="session")
def pack():
    return load_pack("MA000004", date(2026, 10, 18))


@pytest.fixture
def calc(pack):
    # Christmas Day 2026 (Friday) used as a public holiday fixture.
    return Calculator(pack, public_holidays=[date(2026, 12, 25)])


def shift(start: str, end: str, **kw) -> Shift:
    return Shift(start=datetime.fromisoformat(start), end=datetime.fromisoformat(end), **kw)


def profile(emp: str = "casual", shiftworker: bool = False, extended: bool = False, level: int = 1):
    return EmployeeProfile(
        classification=f"retail_employee_level_{level}",
        employment_type=EmploymentType(emp),
        shiftworker=shiftworker,
        extended_trading=extended,
    )
