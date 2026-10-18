"""Underpayment injectors.

Each payslip starts as the correct entitlement (one PaidLine per engine line item). An injector rewrites
some lines the way a real payroll mistake would, including how the employer would *label* them
(e.g. Sunday hours relabelled as ordinary). The generator measures each injector's dollar impact, so
every payslip has exact ground truth for evaluation.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from wagecheck.engine import round_cents
from wagecheck.models import EmploymentType, ShiftResult
from wagecheck.rules.schema import Rate, RulePack


@dataclass(frozen=True)
class PaidLine:
    shift_index: int
    work_date: date
    band: str          # how the employer labelled it
    true_band: str     # what it actually was
    minutes: int
    percent: Decimal
    flat: Decimal      # flat $ per hour (or part) included in the rate
    flat_per_part_hour: bool
    base: Decimal

    @property
    def rate(self) -> Decimal:
        return round_cents(self.base * self.percent / 100 + self.flat)

    @property
    def amount(self) -> Decimal:
        if self.flat and self.flat_per_part_hour:
            started = -(-self.minutes // 60)
            return round_cents(round_cents(self.base * self.percent / 100) * Decimal(self.minutes) / 60
                               + self.flat * started)
        return round_cents(self.rate * Decimal(self.minutes) / 60)


@dataclass
class Context:
    pack: RulePack
    classification: str
    employment: EmploymentType
    results: list[ShiftResult]

    @property
    def casual(self) -> bool:
        return self.employment is EmploymentType.CASUAL

    def ordinary_pct(self) -> Decimal:
        r = self.pack.ordinary
        return r.casual if self.casual else r.ft_pt


def band_rate(pack: RulePack, band: str, classification: str) -> Rate | None:
    """The Rate object behind an engine band name (None for minimum-engagement top-ups)."""
    if band == "ordinary":
        return pack.ordinary
    for tb in pack.time_bands:
        if tb.name == band and (tb.levels is None or classification in tb.levels):
            return tb.rate
    if band.startswith("shiftwork_") and pack.shiftwork is not None:
        return getattr(pack.shiftwork, band.removeprefix("shiftwork_"), None)
    if band.startswith("overtime_"):
        key = band.removeprefix("overtime_")
        key = {"first": "mon_sat_first", "after": "mon_sat_after"}.get(key, key)
        return getattr(pack.overtime, key, None)
    return None


def expected_lines(ctx: Context) -> list[PaidLine]:
    out: list[PaidLine] = []
    base = ctx.pack.classifications[ctx.classification].hourly
    for i, res in enumerate(ctx.results):
        prev_rate: Rate | None = None
        for li in res.lines:
            rate = band_rate(ctx.pack, li.band, ctx.classification) or prev_rate
            flat = rate.flat_per_hour if rate else Decimal(0)
            part = bool(rate and rate.flat_per_part_hour)
            out.append(PaidLine(i, li.work_date, li.band, li.band, li.minutes, li.percent, flat, part, base))
            prev_rate = rate
    return out


def total(lines: list[PaidLine]) -> Decimal:
    return sum((ln.amount for ln in lines), Decimal("0.00"))


@dataclass
class Injection:
    kind: str
    detail: str
    dates: list[str]


Injector = Callable[[list[PaidLine], Context, random.Random], tuple[list[PaidLine], Injection] | None]

PENALTY_PREFIXES = ("overtime", "min_engagement")


def _is_penalty(ln: PaidLine, ctx: Context) -> bool:
    return (ln.true_band != "ordinary" and not ln.true_band.startswith(PENALTY_PREFIXES)
            and (ln.percent > ctx.ordinary_pct() or ln.flat > 0))


def flat_rate(lines: list[PaidLine], ctx: Context, rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """Every hour paid at the ordinary rate: no penalties, loadings or overtime rates."""
    ordp = ctx.ordinary_pct()
    hit = [ln for ln in lines if ln.percent != ordp or ln.flat]
    if not hit:
        return None
    new = [replace(ln, band="ordinary", percent=ordp, flat=Decimal(0)) for ln in lines]
    return new, Injection("flat_rate", "All hours paid at the ordinary rate",
                          sorted({str(ln.work_date) for ln in hit}))


def band_downgrade(lines: list[PaidLine], ctx: Context, rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """One penalty band (e.g. Sunday, Saturday, late night) paid and labelled as ordinary time."""
    bands = sorted({ln.true_band for ln in lines if _is_penalty(ln, ctx)})
    if not bands:
        return None
    target = rng.choice(bands)
    ordp = ctx.ordinary_pct()
    new = [replace(ln, band="ordinary", percent=ordp, flat=Decimal(0)) if ln.true_band == target else ln
           for ln in lines]
    return new, Injection("band_downgrade", f"'{target}' hours paid as ordinary time",
                          sorted({str(ln.work_date) for ln in lines if ln.true_band == target}))


def post_midnight_underpaid(lines: list[PaidLine], ctx: Context,
                            rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """Hours after midnight paid at the rate of the band before midnight (e.g. a Friday-night shift
    whose Saturday hours stay at the weekday rate)."""
    new, dates = list(lines), []
    for i in range(1, len(lines)):
        a, b = lines[i - 1], lines[i]
        if a.shift_index == b.shift_index and b.work_date > a.work_date and (b.percent, b.flat) > (a.percent, a.flat) \
                and not b.true_band.startswith(PENALTY_PREFIXES):
            new[i] = replace(b, band=a.band, percent=a.percent, flat=a.flat, flat_per_part_hour=a.flat_per_part_hour)
            dates.append(str(b.work_date))
    if not dates:
        return None
    return new, Injection("post_midnight_underpaid", "Hours after midnight paid at the pre-midnight rate", dates)


def missing_casual_loading(lines: list[PaidLine], ctx: Context,
                           rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """Casual paid the permanent percentage: the 25% casual loading is left out."""
    if not ctx.casual:
        return None
    new, dates = [], set()
    for ln in lines:
        rate = band_rate(ctx.pack, ln.true_band, ctx.classification)
        ft = rate.ft_pt if rate else ln.percent - ctx.pack.casual_loading_pct
        if ft < ln.percent:
            new.append(replace(ln, percent=ft))
            dates.add(str(ln.work_date))
        else:
            new.append(ln)
    if not dates:
        return None
    return new, Injection("missing_casual_loading", "Casual loading not paid", sorted(dates))


def stale_base_rate(lines: list[PaidLine], ctx: Context,
                    rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """Last year's base rate: the 1 July 2026 increase (4.75%) not applied."""
    old = round_cents(lines[0].base / Decimal("1.0475"))
    new = [replace(ln, base=old) for ln in lines]
    return new, Injection("stale_base_rate", f"Base rate {old} instead of {lines[0].base} (July increase missed)",
                          sorted({str(ln.work_date) for ln in lines}))


def hours_shaved(lines: list[PaidLine], ctx: Context, rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """15 minutes cut from the end of every shift."""
    new = list(lines)
    dates = []
    last_of_shift = {ln.shift_index: i for i, ln in enumerate(lines)}
    for i in last_of_shift.values():
        if new[i].minutes > 15 and not new[i].true_band.startswith("min_engagement"):
            new[i] = replace(new[i], minutes=new[i].minutes - 15)
            dates.append(str(new[i].work_date))
    if not dates:
        return None
    return new, Injection("hours_shaved", "15 minutes unpaid at the end of each shift", dates)


def min_engagement_ignored(lines: list[PaidLine], ctx: Context,
                           rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """A shorter-than-minimum shift paid only for the time actually worked."""
    dates = [str(ln.work_date) for ln in lines if ln.true_band == "min_engagement_topup"]
    if not dates:
        return None
    return [ln for ln in lines if ln.true_band != "min_engagement_topup"], Injection(
        "min_engagement_ignored", "Minimum engagement top-up not paid", dates)


def overtime_as_ordinary(lines: list[PaidLine], ctx: Context,
                         rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """Hours past the daily limit paid at the ordinary rate instead of overtime."""
    ordp = ctx.ordinary_pct()
    dates = [str(ln.work_date) for ln in lines if ln.true_band.startswith("overtime")]
    if not dates:
        return None
    new = [replace(ln, band="ordinary", percent=ordp, flat=Decimal(0)) if ln.true_band.startswith("overtime")
           else ln for ln in lines]
    return new, Injection("overtime_as_ordinary", "Overtime hours paid at the ordinary rate", sorted(set(dates)))


def flat_loading_missing(lines: list[PaidLine], ctx: Context,
                         rng: random.Random) -> tuple[list[PaidLine], Injection] | None:
    """Late-night flat $ per hour loadings (restaurant / hospitality) left off."""
    dates = [str(ln.work_date) for ln in lines if ln.flat > 0]
    if not dates:
        return None
    return [replace(ln, flat=Decimal(0)) for ln in lines], Injection(
        "flat_loading_missing", "Late-night $ per hour loading not paid", sorted(set(dates)))


INJECTORS: dict[str, Injector] = {
    "flat_rate": flat_rate,
    "band_downgrade": band_downgrade,
    "post_midnight_underpaid": post_midnight_underpaid,
    "missing_casual_loading": missing_casual_loading,
    "stale_base_rate": stale_base_rate,
    "hours_shaved": hours_shaved,
    "min_engagement_ignored": min_engagement_ignored,
    "overtime_as_ordinary": overtime_as_ordinary,
    "flat_loading_missing": flat_loading_missing,
}
NEEDS_SHORT_SHIFT = {"min_engagement_ignored"}
NEEDS_LONG_SHIFT = {"overtime_as_ordinary"}
