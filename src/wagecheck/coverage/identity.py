"""Who is the employer and what is the job? Normalised identifiers pulled from a payslip."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

_ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
_LEGAL_SUFFIXES = re.compile(
    r"\b(pty|proprietary|ltd|limited|p/l|inc|incorporated|co|company|the|trading as|t/as)\b\.?"
)


def normalise_abn(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 else None


def abn_is_valid(abn: str) -> bool:
    """ATO checksum: subtract 1 from the first digit, weight, sum, divisible by 89."""
    if not re.fullmatch(r"\d{11}", abn):
        return False
    digits = [int(d) for d in abn]
    digits[0] -= 1
    return sum(d * w for d, w in zip(digits, _ABN_WEIGHTS, strict=True)) % 89 == 0


def normalise_name(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = _LEGAL_SUFFIXES.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class PayslipIdentity(BaseModel):
    """Fields the extractor (Phase 3) will fill. Every field is optional because payslips vary."""

    model_config = ConfigDict(frozen=True)

    employer_name: str | None = None
    trading_name: str | None = None
    abn: str | None = None
    job_title: str | None = None
    classification_text: str | None = None  # e.g. "Retail Employee Level 1" as printed
    instrument_text: str | None = None      # any award / agreement named on the payslip
    workplace_description: str | None = None  # free text from the user: "supermarket", "cafe"

    @field_validator("abn")
    @classmethod
    def _abn(cls, v: str | None) -> str | None:
        return normalise_abn(v)

    def names(self) -> list[str]:
        return [normalise_name(n) for n in (self.employer_name, self.trading_name) if n]

    def job_text(self) -> str:
        parts = (self.job_title, self.classification_text, self.workplace_description,
                 self.trading_name, self.employer_name)
        return " ".join(p for p in parts if p)
