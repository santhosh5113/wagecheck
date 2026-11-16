"""Score extraction against synthetic ground truth.

Header fields: exact match after normalisation (case, spacing and punctuation ignored for text, because
OCR routinely drops spaces; digits only for ABN and IDs; exact for dates and money).
Pay lines: a predicted line counts only if hours, rate and amount are all exactly right; lines are
matched as a multiset, so order and labels don't matter.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from wagecheck.extract.schema import HEADER_FIELDS, ExtractedPayslip
from wagecheck.synth.payslip import Payslip


def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (date, Decimal)):
        return v
    s = re.sub(r"[^a-z0-9]", "", str(v).lower())
    return s or None


def truth_fields(p: Payslip) -> dict[str, Any]:
    return {
        "employer_name": p.employer_name, "employer_abn": re.sub(r"\D", "", p.employer_abn),
        "employee_name": p.employee_name, "employee_id": p.employee_id, "job_title": p.job_title,
        "classification": p.classification_label, "employment_type": p.employment_type,
        "award_named": p.award_named, "period_start": p.period_start, "period_end": p.period_end,
        "pay_date": p.pay_date, "gross": p.gross, "tax": p.tax, "net": p.net, "super_amount": p.super_amount,
    }


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, other: Score) -> None:
        self.tp, self.fp, self.fn = self.tp + other.tp, self.fp + other.fp, self.fn + other.fn

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class DocResult:
    header: Score
    lines: Score
    per_field: dict[str, bool]
    exact: bool  # every header field and every line right


def score_doc(pred: ExtractedPayslip, truth: Payslip) -> DocResult:
    t = truth_fields(truth)
    header, per_field = Score(), {}
    for f in HEADER_FIELDS:
        tv, pv = _norm(t[f]), _norm(getattr(pred, f))
        if f == "employer_abn" and pv is not None:
            pv = re.sub(r"\D", "", str(pv)) or None
        if tv is None and pv is None:
            continue
        ok = tv is not None and pv == tv
        per_field[f] = ok
        if ok:
            header.tp += 1
        else:
            header.fp += pv is not None
            header.fn += tv is not None
    Key = tuple[Decimal | None, Decimal | None, Decimal | None]
    want: Counter[Key] = Counter((ln.hours, ln.rate, ln.amount) for ln in truth.lines)
    got: Counter[Key] = Counter((ln.hours, ln.rate, ln.amount) for ln in pred.lines)
    tp = sum((want & got).values())
    lines = Score(tp, sum(got.values()) - tp, sum(want.values()) - tp)
    exact = header.fp == header.fn == 0 and lines.fp == lines.fn == 0
    return DocResult(header, lines, per_field, exact)


@dataclass
class Report:
    groups: dict[str, list[DocResult]] = field(default_factory=lambda: defaultdict(list))

    def add(self, key: str, r: DocResult) -> None:
        self.groups[key].append(r)
        self.groups["ALL"].append(r)

    def rows(self) -> list[dict[str, Any]]:
        out = []
        for key in sorted(self.groups, key=lambda k: (k != "ALL", k)):
            rs = self.groups[key]
            h, ln = Score(), Score()
            for r in rs:
                h.add(r.header)
                ln.add(r.lines)
            out.append({"group": key, "n": len(rs), "header_f1": round(h.f1, 3), "line_f1": round(ln.f1, 3),
                        "exact_payslips": round(sum(r.exact for r in rs) / len(rs), 3)})
        return out

    def field_accuracy(self) -> dict[str, float]:
        hits: dict[str, list[bool]] = defaultdict(list)
        for r in self.groups.get("ALL", []):
            for f, ok in r.per_field.items():
                hits[f].append(ok)
        return {f: round(sum(v) / len(v), 3) for f, v in hits.items()}
