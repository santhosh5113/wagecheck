"""Rule-based extractor: regular expressions plus column positions. No model, no training.

It is the score an LLM has to beat. It knows common payslip labels and tolerates the usual OCR damage
(spaces dropped between words, ':' or ',' read as a decimal point, S read as $), but nothing more.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from wagecheck.extract.schema import ExtractedLine, ExtractedPayslip
from wagecheck.extract.text import DocText, TextLine

MONEY = re.compile(r"^[$S]\s?-?\d{1,3}(?:[,.]?\d{3})*[.:,]\d{2}$")
NUMBER = re.compile(r"^\d{1,3}(?:[.:,]\d{1,2})?$")
DATE = r"(\d{1,2}/\d{1,2}/\d{4})"
LABELS = [  # (field, pattern) - longer labels first so "Employee ID" wins over "Employee"
    ("employee_id", r"Employee\s*ID\s*:?"),
    ("employment_type", r"Employment\s*type\s*:?"),
    ("employee_name", r"Employee\s*:"),
    ("position", r"Position\s*:?"),
    ("award_named", r"Award\s*:"),
    ("period", r"Pay\s*period\s*:?"),
    ("pay_date", r"Pay\s*date\s*:?"),
    ("abn", r"ABN\s*:?"),
]
TOTALS = [("gross", r"Gross\s*pay"), ("tax", r"PAYG|Tax\s*withheld"), ("net", r"Net\s*pay"),
          ("super_amount", r"Super")]
TABLE_HEADER = re.compile(r"\b(Rate)\b", re.I)


def parse_money(s: str) -> Decimal | None:
    t = s.strip().replace(" ", "")
    if not MONEY.match(t):
        return None
    t = t.lstrip("$S")
    t = t[:-3].replace(",", "").replace(".", "") + "." + t[-2:]  # last separator is the decimal point
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def parse_number(s: str) -> Decimal | None:
    t = s.lower().removesuffix("hrs").removesuffix("hr").strip()
    if not NUMBER.match(t):
        return None
    return Decimal(t.replace(":", ".").replace(",", "."))


def parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


def _header_values(text: str) -> dict[str, str]:
    """Split a header blob into label -> value, cutting each value at the next label."""
    hits: list[tuple[str, int, int]] = []
    for field, pat in LABELS:
        for m in re.finditer(pat, text, re.I):
            if not any(a <= m.start() < b for _, a, b in hits):
                hits.append((field, m.start(), m.end()))
    hits.sort(key=lambda h: h[1])
    out: dict[str, str] = {}
    for i, (field, _start, end) in enumerate(hits):
        stop = hits[i + 1][1] if i + 1 < len(hits) else len(text)
        out.setdefault(field, text[end:stop].strip(" :|"))
    return out


def extract(doc: DocText) -> ExtractedPayslip:
    lines = doc.lines
    p = ExtractedPayslip()
    header_end = next((i for i, ln in enumerate(lines) if TABLE_HEADER.search(ln.text)), len(lines))
    if lines:
        p.employer_name = re.sub(r"\s*PAYSLIP\s*", " ", lines[0].text, flags=re.I).strip() or None

    hv = _header_values("  ".join(ln.text for ln in lines[:header_end]))
    if "abn" in hv:
        digits = re.sub(r"\D", "", hv["abn"])[:11]
        p.employer_abn = digits if len(digits) == 11 else None
    p.employee_name = hv.get("employee_name") or None
    p.employee_id = re.sub(r"\D", "", hv.get("employee_id", "")) or None
    p.employment_type = (hv.get("employment_type") or "").split(" ")[0] or None
    p.award_named = hv.get("award_named") or None
    if pos := hv.get("position"):
        m = re.match(r"(.*?)\s*\((.*)\)\s*$", pos)
        p.job_title, p.classification = (m.group(1), m.group(2)) if m else (pos, None)
    if m := re.search(DATE + r"\s*to\s*" + DATE, hv.get("period", "")):
        p.period_start, p.period_end = parse_date(m.group(1)), parse_date(m.group(2))
    if m := re.search(DATE, hv.get("pay_date", "")):
        p.pay_date = parse_date(m.group(1))

    if header_end < len(lines):
        p.lines = _table(lines[header_end], lines[header_end + 1:])
    for ln in lines[header_end + 1:]:
        for field, pat in TOTALS:
            if re.search(pat, ln.text, re.I) and getattr(p, field) is None:
                money = [parse_money(t) for _, t in ln.tokens]
                vals = [m for m in money if m is not None]
                setattr(p, field, vals[-1] if vals else None)
    return p


def _table(header: TextLine, body: list[TextLine]) -> list[ExtractedLine]:
    cols = {}
    for x, t in header.tokens:
        key = t.lower()
        if key in ("rate",):
            cols["rate"] = x
        elif key in ("amount", "total"):
            cols["amount"] = x
    out = []
    for ln in body:
        if re.search(r"Gross\s*pay", ln.text, re.I):
            break
        monies = [(x, parse_money(t)) for x, t in ln.tokens if parse_money(t) is not None]
        nums = [(x, parse_number(t)) for x, t in ln.tokens if parse_money(t) is None and parse_number(t) is not None]
        if len(monies) < 2 or not nums:
            continue
        rate = amount = None
        if "rate" in cols and "amount" in cols:
            for x, v in monies:
                if abs(x - cols["rate"]) < abs(x - cols["amount"]):
                    rate = v
                else:
                    amount = v
        else:
            rate, amount = monies[0][1], monies[-1][1]
        first_num_x = min(x for x, _ in monies + nums)
        words = [t for x, t in ln.tokens if x < first_num_x]
        code = words.pop(0) if words and re.fullmatch(r"[A-Z0-9]{2,6}", words[0]) and len(words) > 1 else None
        hours = next(v for _, v in nums if v is not None)
        out.append(ExtractedLine(code=code, description=" ".join(words) or None, hours=hours, rate=rate,
                                 amount=amount))
    return out
