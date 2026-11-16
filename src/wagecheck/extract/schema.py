"""What extraction must produce from a payslip, whichever method does it."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, field_validator


def _money(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        d = v
    else:
        s = str(v).replace("$", "").replace(",", "").replace("S", "").strip()
        d = Decimal(s)
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ExtractedLine(BaseModel):
    code: str | None = None
    description: str | None = None
    hours: Decimal | None = None
    rate: Decimal | None = None
    amount: Decimal | None = None

    _norm = field_validator("hours", "rate", "amount", mode="before")(lambda cls, v: _money(v))


class ExtractedPayslip(BaseModel):
    employer_name: str | None = None
    employer_abn: str | None = None
    employee_name: str | None = None
    employee_id: str | None = None
    job_title: str | None = None
    classification: str | None = None
    employment_type: str | None = None
    award_named: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    pay_date: date | None = None
    gross: Decimal | None = None
    tax: Decimal | None = None
    net: Decimal | None = None
    super_amount: Decimal | None = None
    lines: list[ExtractedLine] = []

    _m = field_validator("gross", "tax", "net", "super_amount", mode="before")(lambda cls, v: _money(v))

    def consistency_errors(self) -> list[str]:
        """Arithmetic a correct extraction must satisfy (used to trigger an LLM repair pass)."""
        errs = []
        amounts = [ln.amount for ln in self.lines if ln.amount is not None]
        if self.gross is not None and amounts and abs(sum(amounts, Decimal(0)) - self.gross) > Decimal("0.05"):
            errs.append(f"line amounts sum to {sum(amounts, Decimal(0))} but gross is {self.gross}")
        if None not in (self.gross, self.tax, self.net) and abs(self.gross - self.tax - self.net) > Decimal("0.02"):  # type: ignore[operator]
            errs.append(f"gross {self.gross} - tax {self.tax} != net {self.net}")
        for i, ln in enumerate(self.lines):
            if ln.hours is None or ln.rate is None or ln.amount is None:
                continue
            # flat "per hour or part" loadings make hours x rate inexact, so allow a margin per hour
            if abs(ln.hours * ln.rate - ln.amount) > Decimal("0.30") + ln.hours * Decimal("4.5"):
                errs.append(f"line {i + 1}: {ln.hours} x {ln.rate} is far from {ln.amount}")
        return errs


HEADER_FIELDS = ("employer_name", "employer_abn", "employee_name", "employee_id", "job_title", "classification",
                 "employment_type", "award_named", "period_start", "period_end", "pay_date", "gross", "tax", "net",
                 "super_amount")


def _nullable(t: str, **extra: Any) -> dict[str, Any]:
    return {"anyOf": [{"type": t, **extra}, {"type": "null"}]}


def json_schema() -> dict[str, Any]:
    """Hand-written JSON schema accepted by both Ollama `format` and Claude `output_config.format`
    (no numeric/length constraints, every object closed with additionalProperties: false)."""
    line = {
        "type": "object",
        "properties": {
            "code": _nullable("string"),
            "description": _nullable("string"),
            "hours": _nullable("number"),
            "rate": _nullable("number"),
            "amount": _nullable("number"),
        },
        "required": ["code", "description", "hours", "rate", "amount"],
        "additionalProperties": False,
    }
    props: dict[str, Any] = {f: _nullable("string") for f in HEADER_FIELDS}
    # Dates stay plain strings copied as printed and are parsed in code (llm.parse_date). A `format: date`
    # constraint makes grammar-constrained decoding (Ollama) force YYYY first, which turned 14/12/2026
    # into 1420-12-20 when the model started copying the day.
    for f in ("gross", "tax", "net", "super_amount"):
        props[f] = _nullable("number")
    props["lines"] = {"type": "array", "items": line}
    return {"type": "object", "properties": props, "required": [*HEADER_FIELDS, "lines"],
            "additionalProperties": False}
