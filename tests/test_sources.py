from datetime import date
from pathlib import Path

import httpx
import pytest
from fixtures.make_list_pdf import build
from reportlab.pdfgen import canvas

from wagecheck.cli import main
from wagecheck.coverage.identity import PayslipIdentity
from wagecheck.coverage.resolver import resolve
from wagecheck.sources import catalog
from wagecheck.sources.agreement_index import AgreementIndex, AgreementRecord, parse_list_pdf
from wagecheck.sources.fetcher import Fetched, NeedsUser, PoliteFetcher
from wagecheck.sources.uploads import ingest_agreement

# The real fwc.gov.au robots.txt (Drupal default) disallows /documents/ for every user agent.
FWC_ROBOTS = "User-agent: *\nDisallow: /core/\nDisallow: /documents/\nDisallow: /search/\n"


# ---------------- fetcher ----------------

def make_fetcher(tmp_path, handler, **kw):
    transport = httpx.MockTransport(handler)
    return PoliteFetcher(tmp_path / "cache", transport=transport, sleep=lambda s: None, **kw)


def test_robots_disallow_returns_link_for_user(tmp_path):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=FWC_ROBOTS)
        raise AssertionError(f"must not fetch {req.url}")

    f = make_fetcher(tmp_path, handler)
    res = catalog.fetch_agreement(f, "AE524516")
    assert isinstance(res, NeedsUser)
    assert res.url == "https://www.fwc.gov.au/documents/agreements/fwa/ae524516.pdf"
    assert "robots.txt" in res.reason
    assert isinstance(catalog.fetch_agreement_list(f, 2026), NeedsUser)


def test_allowed_fetch_is_cached_and_revalidated(tmp_path):
    calls = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(dict(req.headers))
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"<html>award</html>", headers={"ETag": '"v1"'})

    f = make_fetcher(tmp_path, handler)
    first = catalog.fetch_award_text(f, "MA000003")
    second = catalog.fetch_award_text(f, "MA000003")
    assert isinstance(first, Fetched) and isinstance(second, Fetched)
    assert not first.from_cache and second.from_cache
    assert first.path.read_bytes() == b"<html>award</html>"
    assert calls[0]["user-agent"].startswith("WageCheck/")


@pytest.mark.parametrize("status", [500, 503])
def test_unreadable_robots_fails_closed(tmp_path, status):
    f = make_fetcher(tmp_path, lambda req: httpx.Response(status))
    res = f.fetch("https://awards.fairwork.gov.au/MA000004.html")
    assert isinstance(res, NeedsUser) and "could not be read" in res.reason


def test_throttle_waits_between_requests(tmp_path):
    waits = []
    t = iter([0.0, 0.0, 0.5, 0.5, 0.6, 0.6, 0.7, 0.7])

    def handler(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, content=b"x")

    f = PoliteFetcher(tmp_path / "c", transport=httpx.MockTransport(handler), min_interval_s=2.0,
                      clock=lambda: next(t), sleep=waits.append)
    f.fetch("https://example.gov.au/a.html")
    assert waits and all(w > 0 for w in waits)


def test_catalog_rejects_bad_identifiers():
    with pytest.raises(ValueError):
        catalog.award_html_url("MA4")
    with pytest.raises(ValueError):
        catalog.agreement_pdf_url("../etc/passwd")


# ---------------- list parsing + index ----------------

@pytest.fixture(scope="module")
def list_pdf(tmp_path_factory):
    return build(tmp_path_factory.mktemp("lists") / "agreements2026.pdf")


@pytest.fixture
def index(list_pdf, tmp_path):
    idx = AgreementIndex(tmp_path / "idx.sqlite")
    records, warnings = parse_list_pdf(list_pdf)
    assert warnings == []
    idx.add(records)
    return idx


def test_parser_rebuilds_wrapped_rows(list_pdf):
    records, warnings = parse_list_pdf(list_pdf)
    assert warnings == []
    assert [r.agreement_id for r in records] == ["AE531861", "AE531723", "AE531722", "AE532224",
                                                  "AE524516", "AE500001"]
    long = records[3]
    assert long.title.startswith("ALL MELBOURNE CONSTRUCTIONS (VIC) TRUST (THE TRUSTEE FOR)")
    assert long.title.endswith("Agreement 2024 - 2027")
    assert long.industry == "Building, metal and civil construction industries"
    assert (long.operative, long.expiry) == (date(2026, 3, 12), date(2027, 7, 2))
    assert records[4].matter == "AG2024/1124"


def test_search_by_employer_name(index):
    hits = index.search(["Coles Supermarkets Australia Pty Ltd"], today=date(2026, 10, 18))
    assert hits[0].agreement_id == "AE524516"
    assert hits[0].url.endswith("/ae524516.pdf")


def test_search_returns_every_site_agreement(index):
    ids = {h.agreement_id for h in index.search(["ALDI Stores"], today=date(2026, 10, 18))}
    assert ids == {"AE531722", "AE531723"}


def test_expired_agreement_is_flagged_not_hidden(index):
    hit = index.search(["Old Corner Store"], today=date(2026, 10, 18))[0]
    assert "nominal expiry" in hit.note


def test_unrelated_name_has_no_hits(index):
    assert index.search(["Little Bean Cafe"]) == []


def test_resolver_surfaces_index_candidates(index):
    r = resolve(PayslipIdentity(employer_name="ALDI Stores", job_title="store assistant",
                                workplace_description="supermarket"), index=index)
    assert r.instrument_id == "MA000004"          # award shown as the fallback...
    assert r.confidence == "low"                  # ...but not trusted while an agreement may apply
    assert {h.agreement_id for h in r.agreement_candidates} == {"AE531722", "AE531723"}


# ---------------- uploads ----------------

def _pdf(path: Path, text: str) -> Path:
    c = canvas.Canvas(str(path))
    c.drawString(72, 750, text)
    c.save()
    return path


def test_upload_verified_when_title_matches(tmp_path):
    rec = AgreementRecord(title="ALDI Jandakot Agreement 2025", agreement_id="AE531723")
    doc = ingest_agreement(_pdf(tmp_path / "a.pdf", "ALDI Jandakot Agreement 2025"), rec, tmp_path / "store")
    assert doc.verified and doc.stored_at.name == "AE531723.pdf"


def test_upload_wrong_document_flagged(tmp_path):
    rec = AgreementRecord(title="ALDI Jandakot Agreement 2025", agreement_id="AE531723")
    doc = ingest_agreement(_pdf(tmp_path / "b.pdf", "Pay guide General Retail Award"), rec, tmp_path / "s")
    assert not doc.verified and "doesn't look like" in doc.message


def test_upload_rejects_non_pdf(tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_text("not a pdf")
    with pytest.raises(ValueError):
        ingest_agreement(bad, AgreementRecord(title="t", agreement_id="AE000001"), tmp_path)


# ---------------- CLI ----------------

def test_cli_build_and_search(list_pdf, tmp_path, capsys):
    db = str(tmp_path / "cli.sqlite")
    assert main(["--db", db, "index", "build", str(list_pdf.parent)]) == 0
    assert "6 agreements indexed" in capsys.readouterr().out
    assert main(["--db", db, "index", "search", "Coles"]) == 0
    assert "AE524516" in capsys.readouterr().out
