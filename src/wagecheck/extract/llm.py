"""LLM extraction behind one switch.

    WAGECHECK_LLM=ollama     (default) local model via Ollama, e.g. qwen2.5:7b-instruct
    WAGECHECK_LLM=anthropic  Claude API (needs ANTHROPIC_API_KEY), default model claude-haiku-5-5
    WAGECHECK_MODEL=...      override the model for either provider

Both providers are given the same JSON schema, so the model can only return the shape we parse.
The output is then checked arithmetically (lines sum to gross, gross - tax = net, hours x rate close
to amount); if a check fails the model gets one repair turn listing the problems.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from wagecheck.extract.schema import ExtractedPayslip, json_schema
from wagecheck.extract.text import DocText

SYSTEM_PROMPT = """You extract data from Australian payslips into JSON.

Rules:
- Use only what is printed. If a field is not on the payslip, use null. Never calculate a missing value.
- The text may come from OCR: fix obvious character errors (e.g. 'l' or 'I' for '1' inside numbers,
  ':' for '.' in money, missing spaces), but do not invent content.
- Dates: copy them exactly as printed (e.g. "14/12/2026" or "14 Dec 2026"). Do not reformat them.
- Money and hours as plain numbers without $ or commas.
- employer_abn: the 11-digit ABN, digits only.
- job_title and classification: if the role reads like "Crew Member (Fast Food Employee Level 1)" or
  "Crew Member - Fast Food Employee Level 1", job_title is "Crew Member" and classification is the level part.
- employment_type: Casual, Part-time or Full-time as printed.
- award_named: the award or industrial instrument name if printed, otherwise null.
- lines: one entry per pay item (hours worked at a rate). Do not include gross, tax, net or super as lines.
  code is a short pay code printed before the description (e.g. ORD, SAT), otherwise null.
- gross is gross/total earnings; tax is tax withheld; net is net/take-home pay; super_amount is superannuation.
"""


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> str: ...


@dataclass
class OllamaProvider:
    model: str = "qwen2.5:7b-instruct"
    host: str = "http://localhost:11434"
    timeout: float = 600.0
    transport: httpx.BaseTransport | None = None
    name: str = "ollama"

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages],
                "format": schema, "stream": False, "options": {"temperature": 0, "num_ctx": 8192}}
        with httpx.Client(timeout=self.timeout, transport=self.transport) as c:
            r = c.post(f"{self.host}/api/chat", json=body)
            r.raise_for_status()
            return str(r.json()["message"]["content"])


@dataclass
class AnthropicProvider:
    model: str = "claude-haiku-5-5"
    api_key: str | None = None
    timeout: float = 120.0
    transport: httpx.BaseTransport | None = None
    name: str = "anthropic"

    def complete(self, system: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("Set ANTHROPIC_API_KEY to use WAGECHECK_LLM=anthropic")
        body = {"model": self.model, "max_tokens": 4096, "system": system, "messages": messages,
                "output_config": {"format": {"type": "json_schema", "schema": schema}}}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        with httpx.Client(timeout=self.timeout, transport=self.transport) as c:
            r = c.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
        if data.get("stop_reason") in ("refusal", "max_tokens"):
            raise RuntimeError(f"Claude stopped early: {data.get('stop_reason')}")
        return str(next(b["text"] for b in data["content"] if b.get("type") == "text"))


def make_provider(name: str | None = None, model: str | None = None) -> Provider:
    name = (name or os.environ.get("WAGECHECK_LLM") or "ollama").lower()
    model = model or os.environ.get("WAGECHECK_MODEL")
    if name == "ollama":
        return OllamaProvider(model=model) if model else OllamaProvider()
    if name == "anthropic":
        return AnthropicProvider(model=model) if model else AnthropicProvider()
    raise ValueError(f"unknown WAGECHECK_LLM {name!r} (use ollama or anthropic)")


_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y")


def parse_date(v: Any) -> date | None:
    """Parse a date as printed on an Australian payslip: day first, unless it is ISO (YYYY-MM-DD)."""
    if not v:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip().rstrip(".")
    s = re.sub(r"(?<=\d)[Il|](?=\d)|(?<![A-Za-z])[Il|](?=\d)", "1", s)  # OCR: l, I or | read for a 1
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)", " ", s)  # OCR drops spaces: 20Jul2026
    s = s.replace("Sept ", "Sep ")
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s.title() if "%b" in fmt or "%B" in fmt else s, fmt).date()
            return d if 2000 <= d.year <= 2100 else None  # a payslip from 1420 is a misread, not a date
        except ValueError:
            continue
    return None


def _clean(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop values that don't parse instead of failing the whole payslip."""
    out = dict(raw)
    for f in ("period_start", "period_end", "pay_date"):
        out[f] = parse_date(out.get(f))
    for f in ("gross", "tax", "net", "super_amount"):
        try:
            out[f] = Decimal(str(out[f])) if out.get(f) not in (None, "") else None
        except InvalidOperation:
            out[f] = None
    lines = []
    for ln in out.get("lines") or []:
        clean = dict(ln)
        for k in ("hours", "rate", "amount"):
            try:
                clean[k] = Decimal(str(clean[k])) if clean.get(k) not in (None, "") else None
            except InvalidOperation:
                clean[k] = None
        lines.append(clean)
    out["lines"] = lines
    if out.get("employer_abn"):
        out["employer_abn"] = "".join(ch for ch in str(out["employer_abn"]) if ch.isdigit()) or None
    return out


@dataclass
class LlmResult:
    payslip: ExtractedPayslip
    attempts: int
    repaired: bool
    remaining_errors: list[str]
    seconds: float
    raw: list[str] = field(default_factory=list)
    final_raw: str = ""  # the model output the payslip was parsed from (cache this, not the parse)


def payslip_from_raw(text: str) -> ExtractedPayslip:
    """Parse model output into a payslip. Kept separate so cached raw output can be re-parsed after a fix."""
    return ExtractedPayslip.model_validate(_clean(json.loads(text)))


class LlmExtractor:
    def __init__(self, provider: Provider, repair: bool = True) -> None:
        self.provider = provider
        self.repair = repair
        self.schema = json_schema()

    def _ask(self, messages: list[dict[str, str]]) -> tuple[ExtractedPayslip | None, str]:
        text = self.provider.complete(SYSTEM_PROMPT, messages, self.schema)
        try:
            return payslip_from_raw(text), text
        except (json.JSONDecodeError, ValueError, TypeError):
            return None, text

    def extract(self, doc: DocText) -> LlmResult:
        t0 = time.monotonic()
        messages = [{"role": "user", "content": f"Payslip text ({doc.source}):\n\n{doc.as_text()}"}]
        p, raw = self._ask(messages)
        raws, attempts = [raw], 1
        if p is None:  # malformed JSON: ask once more from scratch
            p, raw = self._ask(messages)
            raws.append(raw)
            attempts += 1
        if p is None:
            return LlmResult(ExtractedPayslip(), attempts, False, ["unparseable output"], time.monotonic() - t0, raws)
        final_raw = raw
        errors, repaired = p.consistency_errors(), False
        if errors and self.repair:
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": "These checks fail on your extraction:\n- " + "\n- ".join(errors)
                          + "\nRe-read the payslip and return the corrected JSON."}]
            p2, raw2 = self._ask(messages)
            raws.append(raw2)
            attempts += 1
            if p2 is not None and len(p2.consistency_errors()) < len(errors):
                p, errors, repaired, final_raw = p2, p2.consistency_errors(), True, raw2
        return LlmResult(p, attempts, repaired, errors, time.monotonic() - t0, raws, final_raw)
