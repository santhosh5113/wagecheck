"""Payslip model and the way different payroll systems label the same pay.

Three styles, so extraction (Phase 3) has to cope with real-world variety:
- "codes":       short pay codes            ORD, SAT, SUN, NIGHT2, OT15 ...
- "descriptive": plain-English descriptions  Saturday penalty, Overtime first 2 hrs ...
- "percent":     percentage labels           Casual 150%, Ordinary 100% ...
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from wagecheck.engine import round_cents
from wagecheck.synth.errors import PaidLine

Style = Literal["codes", "descriptive", "percent"]
STYLES: tuple[Style, ...] = ("codes", "descriptive", "percent")

_LABELS: dict[str, tuple[str, str]] = {  # band -> (code, description)
    "ordinary": ("ORD", "Ordinary hours"),
    "weekday_evening": ("EVE", "Evening penalty (after 6pm)"),
    "saturday": ("SAT", "Saturday penalty"),
    "sunday": ("SUN", "Sunday penalty"),
    "public_holiday": ("PH", "Public holiday"),
    "shiftwork_weekday": ("NSHIFT", "Night shift"),
    "shiftwork_saturday": ("NSHSAT", "Night shift - Saturday"),
    "shiftwork_sunday": ("NSHSUN", "Night shift - Sunday"),
    "shiftwork_public_holiday": ("NSHPH", "Night shift - public holiday"),
    "late_night_10pm_to_midnight": ("LN1", "Late night 10pm-midnight"),
    "late_night_midnight_to_6am": ("LN2", "Late night midnight-6am"),
    "evening_7pm_to_midnight": ("EVE", "Evening 7pm-midnight"),
    "night_midnight_to_7am": ("NIGHT", "Night midnight-7am"),
    "overtime_first": ("OT15", "Overtime first hours"),
    "overtime_after": ("OT20", "Overtime thereafter"),
    "overtime_saturday_first": ("OTSAT1", "Overtime Saturday first hours"),
    "overtime_saturday_after": ("OTSAT2", "Overtime Saturday thereafter"),
    "overtime_sunday": ("OTSUN", "Overtime Sunday"),
    "overtime_public_holiday": ("OTPH", "Overtime public holiday"),
    "min_engagement_topup": ("MINENG", "Minimum engagement"),
}


def label(band: str, percent: Decimal, casual: bool, style: Style) -> tuple[str, str]:
    code, desc = _LABELS.get(band, (band.upper()[:6], band.replace("_", " ").capitalize()))
    pct = f"{percent.normalize():f}%"
    if style == "codes":
        return code, desc
    if style == "descriptive":
        return "", desc
    return "", f"{'Casual' if casual else 'Permanent'} {desc.lower()} {pct}"


class PayslipLine(BaseModel):
    code: str
    description: str
    hours: Decimal
    rate: Decimal
    amount: Decimal
    band: str  # ground truth only: the employer's band, not printed


class Payslip(BaseModel):
    employer_name: str
    employer_abn: str
    employee_name: str
    employee_id: str
    job_title: str
    classification_label: str
    employment_type: str
    award_named: str | None  # some payslips name the award, many don't
    period_start: date
    period_end: date
    pay_date: date
    lines: list[PayslipLine]
    gross: Decimal
    tax: Decimal
    net: Decimal
    super_amount: Decimal
    style: Style


def aggregate(paid: list[PaidLine], casual: bool, style: Style) -> list[PayslipLine]:
    """Group pay lines the way payroll systems do: one line per pay category and rate."""
    groups: OrderedDict[tuple[str, Decimal], list[PaidLine]] = OrderedDict()
    for ln in paid:
        groups.setdefault((ln.band, ln.rate), []).append(ln)
    out = []
    for (band, rate), lns in groups.items():
        code, desc = label(band, lns[0].percent, casual, style)
        minutes = sum(ln.minutes for ln in lns)
        out.append(PayslipLine(code=code, description=desc, hours=(Decimal(minutes) / 60).quantize(Decimal("0.01")),
                               rate=rate, amount=sum((ln.amount for ln in lns), Decimal("0.00")), band=band))
    return out


SUPER_RATE = Decimal("0.12")  # superannuation guarantee rate from 1 July 2025


def totals(lines: list[PayslipLine]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    gross = sum((ln.amount for ln in lines), Decimal("0.00"))
    ote = sum((ln.amount for ln in lines if not ln.band.startswith("overtime")), Decimal("0.00"))
    tax = round_cents(gross * Decimal("0.17"))  # illustrative only: not a real PAYG withholding calculation
    return gross, tax, gross - tax, round_cents(ote * SUPER_RATE)
