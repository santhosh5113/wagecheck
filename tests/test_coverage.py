import sys
from pathlib import Path

import pytest

from wagecheck.coverage.agreements import is_official, match_registry, search_web
from wagecheck.coverage.awards import rank_awards
from wagecheck.coverage.identity import PayslipIdentity, abn_is_valid, normalise_abn, normalise_name
from wagecheck.coverage.resolver import resolve

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eval_coverage import evaluate  # noqa: E402

COLES_ABN = "45004189708"


# ---- identity ----

def test_abn_checksum():
    assert abn_is_valid(COLES_ABN)
    assert not abn_is_valid("45004189709")
    assert normalise_abn("45 004 189 708") == COLES_ABN
    assert normalise_abn("12 345") is None


def test_name_normalisation_drops_legal_suffixes():
    assert normalise_name("Coles Supermarkets Australia Pty. Ltd.") == "coles supermarkets australia"
    assert normalise_name("Fish & Chips Co") == "fish and chips"


# ---- agreements ----

@pytest.mark.parametrize("ident", [
    PayslipIdentity(abn="45 004 189 708"),
    PayslipIdentity(employer_name="COLES SUPERMARKETS AUSTRALIA PTY LTD"),
    PayslipIdentity(trading_name="Coles Broadway"),
])
def test_registry_finds_coles(ident):
    assert match_registry(ident)[0].agreement.id == "coles-retail-ea-2024"


def test_abn_match_ranked_first():
    m = match_registry(PayslipIdentity(abn=COLES_ABN, trading_name="Coles"))
    assert m[0].matched_on == "abn"


def test_registry_avoids_substring_false_positive():
    assert match_registry(PayslipIdentity(employer_name="Nicoles Cafe")) == []


def test_web_search_keeps_only_official_domains():
    def fake_search(query, domains):
        assert domains == ("fwc.gov.au", "fairwork.gov.au")
        return [
            {"title": "Acme EA", "url": "https://www.fwc.gov.au/documents/agreements/ae1.pdf"},
            {"title": "Blog", "url": "https://fwc.gov.au.evil.example/x"},
            {"title": "Calculator", "url": "https://payrates-blog.com.au/acme"},
        ]
    got = search_web(PayslipIdentity(employer_name="Acme Retail"), fake_search)
    assert [c.title for c in got] == ["Acme EA"]
    assert not is_official("https://notfwc.gov.au/x")


# ---- awards ----

def test_exclusion_pushes_fast_food_away_from_retail():
    ident = PayslipIdentity(job_title="crew member", workplace_description="fast food takeaway store")
    r = rank_awards(ident)
    assert r[0].code == "MA000003"
    retail = next((c for c in r if c.code == "MA000004"), None)
    assert retail is None or "fast food" in retail.excluded_by


def test_award_named_on_payslip_wins():
    r = rank_awards(PayslipIdentity(job_title="team member", instrument_text="General Retail Industry Award"))
    assert r[0].code == "MA000004"


# ---- resolver ----

def test_coles_resolves_to_agreement_needing_a_pack():
    r = resolve(PayslipIdentity(abn=COLES_ABN, job_title="night fill", workplace_description="supermarket"))
    assert (r.instrument_type, r.instrument_id, r.confidence) == ("enterprise_agreement",
                                                                  "coles-retail-ea-2024", "high")
    assert r.rate_source == "agreement_pack_needed"
    assert r.requires_confirmation


def test_coles_warehouse_worker_flagged_low_confidence():
    r = resolve(PayslipIdentity(employer_name="Coles Supermarkets Australia Pty Ltd",
                                job_title="forklift driver", workplace_description="distribution centre"))
    assert r.confidence == "low"
    assert any("MA000084" in w for w in r.warnings)


def test_independent_supermarket_uses_local_rule_pack():
    r = resolve(PayslipIdentity(employer_name="Harbour Fresh Grocers", job_title="night fill",
                                workplace_description="supermarket"))
    assert (r.instrument_id, r.rate_source) == ("MA000004", "rule_pack")
    assert any("Document Search" in w for w in r.warnings)


def test_cafe_now_has_a_full_rule_pack():
    r = resolve(PayslipIdentity(job_title="barista", workplace_description="cafe"))
    assert (r.instrument_id, r.rate_source) == ("MA000119", "rule_pack")


def test_award_without_local_pack_falls_back_to_mapd():
    r = resolve(PayslipIdentity(job_title="cleaner", workplace_description="commercial cleaning company"))
    assert (r.instrument_id, r.rate_source) == ("MA000022", "mapd_rates_only")


def test_bad_abn_is_warned():
    r = resolve(PayslipIdentity(abn="45004189709", job_title="cashier", workplace_description="shop"))
    assert any("checksum" in w for w in r.warnings)


def test_nothing_matches():
    r = resolve(PayslipIdentity(job_title="astronaut", workplace_description="space station"))
    assert (r.instrument_type, r.rate_source, r.confidence) == ("unknown", "unresolved", "low")


# ---- evaluation regression guards ----

def test_dev_set_accuracy(capsys):
    assert evaluate("cases")["top1"] >= 0.95


def test_held_out_accuracy_floor(capsys):
    # Baseline at time of writing: top1 75%, top3 83%. Guard against regressions only.
    res = evaluate("held_out")
    assert res["top1"] >= 0.75 and res["top3"] >= 0.80
