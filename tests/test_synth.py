"""Phase 2: the synthetic payslip generator. Ground truth has to be exact, or every later evaluation
number is meaningless, so these tests check the bookkeeping, not just that files appear."""

import json
import random
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

import pymupdf
import pytest

from wagecheck.cli import main
from wagecheck.coverage.identity import abn_is_valid, normalise_abn
from wagecheck.engine import Calculator
from wagecheck.holidays import nsw_holidays
from wagecheck.models import EmployeeProfile, EmploymentType, Shift
from wagecheck.rules.loader import load_pack
from wagecheck.synth.errors import INJECTORS, Context, expected_lines, post_midnight_underpaid, total
from wagecheck.synth.generate import check_sample, fake_abn, generate, generate_one
from wagecheck.synth.render import SYNTH_NOTE, render_pdf, render_scan


@pytest.fixture(scope="module")
def samples():
    return generate(250, seed=7)


def test_every_sample_satisfies_ground_truth_invariants(samples):
    assert [p for s in samples for p in check_sample(s)] == []


def test_generation_is_deterministic():
    assert generate_one(3, seed=11).model_dump_json() == generate_one(3, seed=11).model_dump_json()
    assert generate_one(3, seed=11).model_dump_json() != generate_one(3, seed=12).model_dump_json()


def test_dataset_covers_all_awards_errors_and_styles(samples):
    assert {s.award for s in samples} == {"MA000004", "MA000003", "MA000119", "MA000009"}
    assert set(Counter(e.kind for s in samples for e in s.errors)) == set(INJECTORS)
    assert {s.payslip.style for s in samples} == {"codes", "descriptive", "percent"}
    clean = sum(not s.errors for s in samples) / len(samples)
    assert 0.2 < clean < 0.5


def test_expected_lines_reproduce_the_engine_exactly(samples):
    for s in samples[:40]:
        pack = load_pack(s.award, s.payslip.period_start)
        profile = EmployeeProfile(classification=s.classification, employment_type=EmploymentType(s.employment),
                                  shiftworker=s.shiftworker)
        calc = Calculator(pack, nsw_holidays())
        assert sum((calc.calculate(sh, profile).total for sh in s.shifts), Decimal(0)) == s.expected_gross


def test_synthetic_abns_can_never_be_real():
    rng = random.Random(0)
    for _ in range(300):
        assert not abn_is_valid(normalise_abn(fake_abn(rng)))


def test_post_midnight_error_reproduces_the_friday_night_case():
    # Casual retail night fill, Friday 10pm to Saturday 6am: Saturday hours left at the weekday 155%.
    pack = load_pack("MA000004", date(2026, 10, 23))
    profile = EmployeeProfile(employment_type=EmploymentType.CASUAL, shiftworker=True)
    res = Calculator(pack).calculate(Shift(start=datetime(2026, 10, 23, 22), end=datetime(2026, 10, 24, 6)), profile)
    ctx = Context(pack, "retail_employee_level_1", EmploymentType.CASUAL, [res])
    lines = expected_lines(ctx)
    new, inj = post_midnight_underpaid(lines, ctx, random.Random(0))
    assert total(lines) - total(new) == (Decimal("48.67") - Decimal("43.11")) * 6
    assert new[-1].band == "shiftwork_weekday" and new[-1].true_band == "shiftwork_saturday"
    assert inj.dates == ["2026-10-24"]


def test_errors_relabel_lines_like_a_real_payroll_mistake(samples):
    flat = next(s for s in samples if [e.kind for e in s.errors] == ["flat_rate"])
    assert {ln.band for ln in flat.payslip.lines} == {"ordinary"}
    assert flat.shortfall > 0


def test_pdf_and_scan_render(samples, tmp_path):
    s = samples[0]
    pdf = render_pdf(s.payslip, tmp_path / "p.pdf")
    with pymupdf.open(pdf) as doc:
        text = doc[0].get_text()
    assert f"${s.payslip.gross:,.2f}" in text and SYNTH_NOTE in text and s.payslip.employer_name in text
    jpg = render_scan(pdf, tmp_path / "p.jpg", random.Random(1))
    assert jpg.read_bytes()[:3] == b"\xff\xd8\xff"


def test_cli_writes_dataset(tmp_path, capsys):
    assert main(["synth", "--n", "5", "--seed", "3", "--out", str(tmp_path)]) == 0
    rows = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert len(rows) == 5
    truth = json.loads((tmp_path / rows[0]["id"] / "truth.json").read_text())
    assert Decimal(truth["expected_gross"]) - Decimal(truth["paid_gross"]) == Decimal(truth["shortfall"])
    assert (tmp_path / rows[0]["id"] / "roster.csv").read_text().startswith("date,start,end")
