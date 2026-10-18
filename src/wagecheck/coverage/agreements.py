"""Find an enterprise agreement that covers an employer.

Two sources, tried in order:
1. A curated local registry (fast, offline, every entry checked by hand).
2. Optional web search restricted to official FWC domains. The FWC has no agreements API,
   so this returns *candidates* for the user to confirm, never an automatic match.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import lru_cache
from importlib import resources
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict

from wagecheck.coverage.identity import PayslipIdentity, normalise_name

OFFICIAL_DOMAINS = ("fwc.gov.au", "fairwork.gov.au")


class AgreementEmployer(BaseModel):
    model_config = ConfigDict(frozen=True)
    legal_name: str
    abn: str | None = None
    trading_names: list[str] = []


class Agreement(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    title: str
    fwc_matter: str
    url: str
    commenced: date
    employers: list[AgreementEmployer]
    coverage_note: str
    underlying_award: str | None = None
    verified_on: date


class AgreementMatch(BaseModel):
    agreement: Agreement
    matched_on: Literal["abn", "legal_name", "trading_name"]
    evidence: str


class WebCandidate(BaseModel):
    title: str
    url: str
    snippet: str = ""


# A search function: (query, allowed_domains) -> list of {"title","url","snippet"} dicts.
SearchFn = Callable[[str, tuple[str, ...]], list[dict[str, str]]]


@lru_cache(maxsize=1)
def load_registry() -> tuple[Agreement, ...]:
    text = resources.files("wagecheck.coverage.data").joinpath("agreements.yaml").read_text("utf-8")
    return tuple(Agreement.model_validate(a) for a in yaml.safe_load(text)["agreements"])


def _name_hit(candidate: str, names: list[str]) -> bool:
    c = normalise_name(candidate)
    # whole-token match so "coles" matches "coles broadway" but not "nicoles cafe"
    return any(n == c or n.startswith(c + " ") or f" {c} " in f" {n} " for n in names)


def match_registry(
    identity: PayslipIdentity, registry: tuple[Agreement, ...] | None = None
) -> list[AgreementMatch]:
    registry = registry if registry is not None else load_registry()
    names = identity.names()
    out: list[AgreementMatch] = []
    for ag in registry:
        for emp in ag.employers:
            if identity.abn and emp.abn and identity.abn == emp.abn:
                out.append(AgreementMatch(agreement=ag, matched_on="abn", evidence=f"ABN {emp.abn}"))
                break
            if names and _name_hit(emp.legal_name, names):
                out.append(AgreementMatch(agreement=ag, matched_on="legal_name", evidence=emp.legal_name))
                break
            hit = next((t for t in emp.trading_names if names and _name_hit(t, names)), None)
            if hit:
                out.append(AgreementMatch(agreement=ag, matched_on="trading_name", evidence=hit))
                break
    order = {"abn": 0, "legal_name": 1, "trading_name": 2}
    return sorted(out, key=lambda m: order[m.matched_on])


def is_official(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


def search_web(identity: PayslipIdentity, search: SearchFn) -> list[WebCandidate]:
    """Ask a search backend for agreements, keeping only results on official FWC/FWO domains."""
    term = identity.abn or identity.employer_name or identity.trading_name
    if not term:
        return []
    query = f'"{term}" enterprise agreement'
    results = search(query, OFFICIAL_DOMAINS)
    return [WebCandidate(**r) for r in results if is_official(r.get("url", ""))]
