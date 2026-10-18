from datetime import date
from decimal import Decimal

import pytest

from wagecheck.engine import round_cents
from wagecheck.rules.loader import available_packs, load_pack


def test_pack_loads_and_is_self_consistent(pack):
    assert pack.instrument_id == "MA000004"
    assert pack.integrity_errors() == []


def test_level_rates_match_fwo_pay_guide_2026(pack):
    # FWO pay guide MA000004, effective 01/07/2026
    assert pack.classifications["retail_employee_level_1"].hourly == Decimal("27.81")
    assert pack.classifications["retail_employee_level_8"].hourly == Decimal("33.99")


@pytest.mark.parametrize(
    ("pct", "expected"),
    [(125, "34.76"), (150, "41.72"), (155, "43.11"), (175, "48.67"), (200, "55.62"), (250, "69.53")],
)
def test_level1_derived_rates_match_pay_guide(pack, pct, expected):
    base = pack.classifications["retail_employee_level_1"].hourly
    assert round_cents(base * pct / 100) == Decimal(expected)


def test_no_pack_before_first_effective_date():
    assert available_packs("MA000004")
    with pytest.raises(LookupError):
        load_pack("MA000004", date(2020, 1, 1))
