"""Price one casual night-fill shift under MA000004 and print the line items.

    python scripts/demo_calc.py
"""

from datetime import date, datetime

from wagecheck.engine import Calculator
from wagecheck.models import EmployeeProfile, EmploymentType, Shift
from wagecheck.rules.loader import load_pack

pack = load_pack("MA000004", date(2026, 10, 23))
calc = Calculator(pack)
result = calc.calculate(
    Shift(start=datetime(2026, 10, 23, 22, 0), end=datetime(2026, 10, 24, 6, 0)),
    EmployeeProfile(employment_type=EmploymentType.CASUAL, shiftworker=True),
)

print(f"{pack.title} — shiftwork: {result.is_shiftwork}")
print(f"{'date':<11}{'band':<22}{'hrs':>6}{'%':>6}{'rate':>9}{'amount':>10}  clauses")
for li in result.lines:
    print(f"{li.work_date!s:<11}{li.band:<22}{li.hours:>6}{li.percent:>6}{li.rate:>9}{li.amount:>10}  "
          f"{', '.join(li.clauses)}")
print(f"{'TOTAL':<54}{result.total:>10}")
for n in result.notes:
    print("note:", n)
