"""Decide which industrial instrument governs a payslip, and where its rates come from.

Order of precedence (mirrors how Australian workplace law works):
1. An approved enterprise agreement covering the employer applies instead of the award.
2. Otherwise the modern award whose coverage best fits the industry and job.

Coverage is a legal judgement. The resolver always returns evidence and sets
`requires_confirmation=True`; nothing downstream runs until the user confirms.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from wagecheck.coverage.agreements import AgreementMatch, SearchFn, WebCandidate, match_registry, search_web
from wagecheck.coverage.awards import AwardCandidate, Reranker, rank_awards
from wagecheck.coverage.identity import PayslipIdentity, abn_is_valid
from wagecheck.rules.loader import available_packs
from wagecheck.sources.agreement_index import AgreementIndex, IndexHit

RateSource = Literal[
    "rule_pack",              # local hand-verified pack: rates + conditions, cross-checked with MAPD
    "mapd_rates_only",        # award with no local pack: MAPD gives rates, conditions not modelled yet
    "agreement_pack",         # enterprise agreement with a local pack
    "agreement_pack_needed",  # agreement found, its rates must be parsed from the agreement document
    "unresolved",
]


class Resolution(BaseModel):
    instrument_type: Literal["enterprise_agreement", "modern_award", "unknown"]
    instrument_id: str | None
    title: str | None
    rate_source: RateSource
    confidence: Literal["high", "medium", "low"]
    evidence: list[str]
    alternatives: list[AwardCandidate] = []
    web_candidates: list[WebCandidate] = []
    agreement_candidates: list[IndexHit] = []  # from the local FWC agreement index
    warnings: list[str] = []
    requires_confirmation: bool = True


def _award_rate_source(code: str) -> RateSource:
    return "rule_pack" if available_packs(code) else "mapd_rates_only"


def _agreement_rate_source(agreement_id: str) -> RateSource:
    return "agreement_pack" if available_packs(agreement_id) else "agreement_pack_needed"


def resolve(
    identity: PayslipIdentity,
    search: SearchFn | None = None,
    reranker: Reranker | None = None,
    index: AgreementIndex | None = None,
) -> Resolution:
    warnings: list[str] = []
    if identity.abn and not abn_is_valid(identity.abn):
        warnings.append(f"ABN {identity.abn} fails the ATO checksum; check the extraction.")

    awards = rank_awards(identity, top_k=3)
    if reranker is not None and awards:
        awards = reranker.rerank(identity, awards)

    # 1. Enterprise agreement in the curated registry
    matches: list[AgreementMatch] = match_registry(identity)
    if matches:
        m = matches[0]
        ag = m.agreement
        conf: Literal["high", "medium", "low"] = "high" if m.matched_on in ("abn", "legal_name") else "medium"
        evidence = [f"Employer matched on {m.matched_on}: {m.evidence}", f"{ag.title} ({ag.fwc_matter})",
                    ag.coverage_note]
        if ag.underlying_award and awards and awards[0].code != ag.underlying_award:
            # e.g. a Coles distribution-centre picker is not covered by the store agreement
            warnings.append(f"Job looks like {awards[0].code}, but this agreement was made against "
                            f"{ag.underlying_award}; the worker may be under a different agreement.")
            conf = "low"
        return Resolution(
            instrument_type="enterprise_agreement", instrument_id=ag.id, title=ag.title,
            rate_source=_agreement_rate_source(ag.id), confidence=conf, evidence=evidence,
            alternatives=awards, warnings=warnings,
        )

    # 2. Not in the registry: the local FWC agreement index, then optional web search
    hits: list[IndexHit] = index.search(identity.names()) if index and identity.names() else []
    web: list[WebCandidate] = search_web(identity, search) if search else []
    if hits:
        warnings.append(f"{len(hits)} possible enterprise agreement(s) found in the FWC agreement list. "
                        "If one covers this job it replaces the award: download it from the link, upload "
                        "it, and confirm before checking pay.")
    elif web:
        warnings.append("Possible enterprise agreement found on fwc.gov.au. If it applies, it replaces "
                        "the award; confirm before checking pay.")
    elif index is None and search is None:
        warnings.append("Agreement index not built and web search is off. Large employers often have "
                        "an enterprise agreement; check FWC Document Search.")

    # 3. Modern award
    if not awards:
        return Resolution(instrument_type="unknown", instrument_id=None, title=None, rate_source="unresolved",
                          confidence="low", evidence=["No award profile matched the job details."],
                          web_candidates=web, agreement_candidates=hits, warnings=warnings)
    top = awards[0]
    gap = top.confidence - (awards[1].confidence if len(awards) > 1 else 0.0)
    confidence: Literal["high", "medium", "low"] = (
        "high" if top.confidence >= 0.75 or gap >= 0.5 else "medium" if gap >= 0.2 else "low"
    )
    if web or hits:
        confidence = "low"
    evidence = [f"Matched: {', '.join(top.matched)}"]
    if top.excluded_by:
        evidence.append(f"Counter-signals: {', '.join(top.excluded_by)}")
    return Resolution(
        instrument_type="modern_award", instrument_id=top.code, title=top.title,
        rate_source=_award_rate_source(top.code), confidence=confidence, evidence=evidence,
        alternatives=awards[1:], web_candidates=web, agreement_candidates=hits, warnings=warnings,
    )
