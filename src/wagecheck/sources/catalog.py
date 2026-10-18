"""Where each official document lives, and how WageCheck gets it.

| Document              | Route                                                              |
|-----------------------|--------------------------------------------------------------------|
| Award pay rates       | MAPD API (wagecheck.mapd): the sanctioned programmatic route         |
| Award text            | awards.fairwork.gov.au/<code>.html, fetched only if robots allow     |
| Yearly agreement list | fwc.gov.au/documents/... : robots.txt disallows /documents/, so the  |
|                       | user downloads it once and WageCheck indexes it locally              |
| Agreement PDF         | fwc.gov.au/documents/agreements/fwa/<ae id>.pdf : same, user uploads |

URL patterns were observed on the live sites on 2026-10-18. The fetcher re-checks robots.txt
at run time, so if a site's rules change, behaviour changes with them.
"""

from __future__ import annotations

import re
from datetime import date

from wagecheck.sources.fetcher import Fetched, NeedsUser, PoliteFetcher

AWARD_HTML = "https://awards.fairwork.gov.au/{code}.html"
AGREEMENT_LIST_PDF = "https://www.fwc.gov.au/documents/agreements/resources/agreements{year}.pdf"
AGREEMENT_PDF = "https://www.fwc.gov.au/documents/agreements/fwa/{agreement_id}.pdf"
DOCUMENT_SEARCH = "https://www.fwc.gov.au/document-search?search-ui=agreements"

_AWARD_CODE = re.compile(r"^MA\d{6}$")
_AGREEMENT_ID = re.compile(r"^A[A-Z]\d{6}$")


def award_html_url(code: str) -> str:
    if not _AWARD_CODE.match(code):
        raise ValueError(f"not an award code: {code!r}")
    return AWARD_HTML.format(code=code)


def agreement_list_url(year: int) -> str:
    if not 2005 <= year <= date.today().year:
        raise ValueError(f"no agreement list for {year}")
    return AGREEMENT_LIST_PDF.format(year=year)


def agreement_pdf_url(agreement_id: str) -> str:
    aid = agreement_id.upper()
    if not _AGREEMENT_ID.match(aid):
        raise ValueError(f"not an agreement id: {agreement_id!r}")
    return AGREEMENT_PDF.format(agreement_id=aid.lower())


def fetch_award_text(fetcher: PoliteFetcher, code: str) -> Fetched | NeedsUser:
    return fetcher.fetch(award_html_url(code))


def fetch_agreement_list(fetcher: PoliteFetcher, year: int) -> Fetched | NeedsUser:
    return fetcher.fetch(agreement_list_url(year))


def fetch_agreement(fetcher: PoliteFetcher, agreement_id: str) -> Fetched | NeedsUser:
    return fetcher.fetch(agreement_pdf_url(agreement_id))
