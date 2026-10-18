"""Rank which modern award most likely covers a job.

Baseline ranker: weighted phrase matching against curated coverage profiles, with IDF so that
terms shared by many awards (e.g. "cook", "packer") count for less, and exclusion terms that
point away from an award. It is deliberately simple, explainable and offline; an LLM reranker
can sit on top through the `Reranker` protocol.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from importlib import resources
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict

from wagecheck.coverage.identity import PayslipIdentity

INDUSTRY_W = 2.0
OCCUPATION_W = 1.5
EXCLUDE_W = 2.5
NAMED_ON_PAYSLIP_W = 20.0


class AwardProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    title: str
    industry: list[str]
    occupation: list[str]
    excludes: list[str] = []


class AwardCandidate(BaseModel):
    code: str
    title: str
    score: float
    confidence: float
    matched: list[str]
    excluded_by: list[str]


class Reranker(Protocol):
    def rerank(self, identity: PayslipIdentity, candidates: list[AwardCandidate]) -> list[AwardCandidate]: ...


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _has(text: str, phrase: str) -> bool:
    return f" {_norm(phrase).strip()} " in text


@lru_cache(maxsize=1)
def load_profiles() -> tuple[AwardProfile, ...]:
    raw = resources.files("wagecheck.coverage.data").joinpath("awards.yaml").read_text("utf-8")
    return tuple(AwardProfile.model_validate(a) for a in yaml.safe_load(raw)["awards"])


@lru_cache(maxsize=1)
def _idf() -> dict[str, float]:
    profiles = load_profiles()
    df: Counter[str] = Counter()
    for p in profiles:
        for t in set(p.industry) | set(p.occupation):
            df[t] += 1
    n = len(profiles)
    return {t: math.log(1 + n / c) / math.log(1 + n) for t, c in df.items()}


def rank_awards(identity: PayslipIdentity, top_k: int = 3) -> list[AwardCandidate]:
    text = _norm(identity.job_text())
    named = _norm(identity.instrument_text or "")
    idf = _idf()
    scored: list[AwardCandidate] = []
    for p in load_profiles():
        score, matched, excluded = 0.0, [], []
        if p.code.lower() in named or _has(named, p.title.split(" Award")[0]):
            score += NAMED_ON_PAYSLIP_W
            matched.append(f"named on payslip: {p.code}")
        for weight, terms in ((INDUSTRY_W, p.industry), (OCCUPATION_W, p.occupation)):
            for t in terms:
                if _has(text, t):
                    score += weight * idf[t] * (1 + 0.25 * (len(t.split()) - 1))
                    matched.append(t)
        for t in p.excludes:
            if _has(text, t):
                score -= EXCLUDE_W
                excluded.append(t)
        if score > 0:
            scored.append(AwardCandidate(code=p.code, title=p.title, score=round(score, 3),
                                         confidence=0.0, matched=matched, excluded_by=excluded))
    total = sum(c.score for c in scored)
    for c in scored:
        c.confidence = round(c.score / total, 3) if total else 0.0
    return sorted(scored, key=lambda c: c.score, reverse=True)[:top_k]
