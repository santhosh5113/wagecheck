"""Phase 3: extraction. The LLM is replaced by fake providers here (no network, no model in CI);
the real model is evaluated with `wagecheck eval-extract --method llm` on a laptop."""

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from wagecheck.cli import main
from wagecheck.extract import baseline
from wagecheck.extract.evaluate import Report, score_doc
from wagecheck.extract.llm import (
    AnthropicProvider,
    LlmExtractor,
    OllamaProvider,
    make_provider,
    parse_date,
    payslip_from_raw,
)
from wagecheck.extract.schema import ExtractedLine, ExtractedPayslip, json_schema
from wagecheck.extract.text import DocText, from_pdf, load
from wagecheck.synth.generate import generate
from wagecheck.synth.render import render_pdf, render_scan


def oracle(p) -> ExtractedPayslip:
    """A perfect extraction built from ground truth."""
    job, cls = p.job_title, p.classification_label
    return ExtractedPayslip(
        employer_name=p.employer_name, employer_abn=p.employer_abn.replace(" ", ""), employee_name=p.employee_name,
        employee_id=p.employee_id, job_title=job, classification=cls, employment_type=p.employment_type,
        award_named=p.award_named, period_start=p.period_start, period_end=p.period_end, pay_date=p.pay_date,
        gross=p.gross, tax=p.tax, net=p.net, super_amount=p.super_amount,
        lines=[ExtractedLine(code=ln.code or None, description=ln.description, hours=ln.hours, rate=ln.rate,
                             amount=ln.amount) for ln in p.lines])


@pytest.fixture(scope="module")
def samples():
    return generate(24, seed=5)


@pytest.fixture(scope="module")
def pdfs(samples, tmp_path_factory):
    d = tmp_path_factory.mktemp("pdfs")
    return [(s, render_pdf(s.payslip, d / f"{s.id}.pdf")) for s in samples]


# ---------------- scoring ----------------

def test_oracle_scores_perfectly(samples):
    for s in samples:
        r = score_doc(oracle(s.payslip), s.payslip)
        assert r.exact and r.header.f1 == 1.0 and r.lines.f1 == 1.0


def test_scoring_penalises_missing_and_wrong_values(samples):
    p = samples[0].payslip
    pred = oracle(p).model_copy(update={"gross": None, "lines": [*oracle(p).lines[1:], ExtractedLine(
        hours=Decimal("1.00"), rate=Decimal("1.00"), amount=Decimal("1.00"))]})
    r = score_doc(pred, p)
    assert not r.exact and r.per_field["gross"] is False
    assert (r.lines.tp, r.lines.fp, r.lines.fn) == (len(p.lines) - 1, 1, 1)


def test_text_normalisation_ignores_case_and_spacing(samples):
    p = samples[0].payslip
    pred = oracle(p).model_copy(update={"employer_name": p.employer_name.upper().replace(" ", "")})
    assert score_doc(pred, p).per_field["employer_name"]


# ---------------- text layer + baseline ----------------

def test_baseline_is_exact_on_standard_pdfs(pdfs):
    report = Report()
    for s, pdf in pdfs:
        if s.payslip.layout == "standard":
            report.add("pdf", score_doc(baseline.extract(from_pdf(pdf)), s.payslip))
    row = report.rows()[0]
    assert row["n"] >= 10 and row["header_f1"] == 1.0 and row["line_f1"] == 1.0


def test_baseline_fails_on_the_held_out_layout(pdfs):
    alt = [(s, pdf) for s, pdf in pdfs if s.payslip.layout == "alt"]
    assert alt, "fixture should contain held-out layouts"
    for s, pdf in alt:
        assert score_doc(baseline.extract(from_pdf(pdf)), s.payslip).lines.tp == 0


def test_ocr_rebuilds_rows_on_a_tilted_scan(pdfs, tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    import random

    s, pdf = next((s, p) for s, p in pdfs if s.payslip.layout == "standard" and len(s.payslip.lines) >= 3)
    scan = render_scan(pdf, tmp_path / "scan.jpg", random.Random(3))
    doc = load(scan)
    assert doc.source == "ocr"
    assert score_doc(baseline.extract(doc), s.payslip).lines.recall >= 0.6


def test_baseline_tolerates_ocr_money_damage():
    assert baseline.parse_money("$41:72") == Decimal("41.72")
    assert baseline.parse_money("S1,234.50") == Decimal("1234.50")
    assert baseline.parse_money("hello") is None


# ---------------- schema ----------------

def test_schema_fits_both_providers():
    def walk(node):
        if isinstance(node, dict):
            for bad in ("minimum", "maximum", "minLength", "maxLength", "multipleOf"):
                assert bad not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json_schema())


# ---------------- LLM extractor with fake providers ----------------

class Scripted:
    name, model = "fake", "fake"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, messages, schema):
        self.calls.append(messages)
        return self.replies.pop(0)


def _json(p: ExtractedPayslip) -> str:
    return p.model_dump_json()


def test_llm_output_is_parsed_and_checked(samples, pdfs):
    s, pdf = pdfs[0]
    ex = LlmExtractor(Scripted([_json(oracle(s.payslip))]))
    res = ex.extract(from_pdf(pdf))
    assert res.attempts == 1 and not res.repaired and res.remaining_errors == []
    assert score_doc(res.payslip, s.payslip).exact


def test_inconsistent_output_triggers_one_repair_turn(samples, pdfs):
    s, pdf = next((s, p) for s, p in pdfs if len(s.payslip.lines) >= 2)
    good = oracle(s.payslip)
    bad = good.model_copy(update={"lines": good.lines[1:]})  # a dropped line breaks "lines sum to gross"
    fake = Scripted([_json(bad), _json(good)])
    res = LlmExtractor(fake).extract(from_pdf(pdf))
    assert res.repaired and res.attempts == 2 and res.remaining_errors == []
    assert "line amounts sum to" in fake.calls[1][-1]["content"]


def test_garbage_output_degrades_gracefully(pdfs):
    res = LlmExtractor(Scripted(["not json", "{still not"])).extract(from_pdf(pdfs[0][1]))
    assert res.remaining_errors == ["unparseable output"] and res.payslip.lines == []


def test_bad_values_are_dropped_not_fatal(pdfs):
    raw = {"period_start": "17th of never", "gross": "lots", "lines": [{"hours": "x", "rate": 1, "amount": 2}]}
    res = LlmExtractor(Scripted([json.dumps(raw)]), repair=False).extract(from_pdf(pdfs[0][1]))
    assert res.payslip.period_start is None and res.payslip.gross is None and res.payslip.lines[0].hours is None


# ---------------- providers (HTTP mocked) ----------------

def test_ollama_request_shape():
    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}})

    out = OllamaProvider(transport=httpx.MockTransport(h)).complete("sys", [{"role": "user", "content": "x"}],
                                                                    {"type": "object"})
    assert out == '{"ok": true}'
    assert seen["format"] == {"type": "object"} and seen["options"]["temperature"] == 0 and not seen["stream"]
    assert seen["messages"][0] == {"role": "system", "content": "sys"}


def test_anthropic_request_shape(monkeypatch):
    seen = {}

    def h(req):
        seen["headers"], seen["body"] = req.headers, json.loads(req.content)
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}]})

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    out = AnthropicProvider(transport=httpx.MockTransport(h)).complete("sys", [{"role": "user", "content": "x"}],
                                                                       {"type": "object"})
    assert out == "{}"
    assert seen["headers"]["x-api-key"] == "test-key" and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["model"] == "claude-haiku-5-5"
    assert seen["body"]["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}


def test_anthropic_refusal_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"stop_reason": "refusal", "content": []}))
    with pytest.raises(RuntimeError, match="refusal"):
        AnthropicProvider(transport=t).complete("s", [], {})


def test_one_switch_selects_the_provider(monkeypatch):
    monkeypatch.delenv("WAGECHECK_LLM", raising=False)
    monkeypatch.delenv("WAGECHECK_MODEL", raising=False)
    assert make_provider().name == "ollama" and make_provider().model == "qwen2.5:7b-instruct"
    monkeypatch.setenv("WAGECHECK_LLM", "anthropic")
    assert make_provider().model == "claude-haiku-5-5"
    monkeypatch.setenv("WAGECHECK_MODEL", "claude-sonnet-5-5")
    assert make_provider().model == "claude-sonnet-5-5"
    with pytest.raises(ValueError):
        make_provider("gpt")


# ---------------- CLI ----------------

def test_eval_extract_cli_on_baseline(tmp_path, capsys):
    data = tmp_path / "synth"
    assert main(["synth", "--n", "6", "--seed", "9", "--out", str(data)]) == 0
    out = tmp_path / "report.json"
    assert main(["eval-extract", "--method", "baseline", "--source", "pdf", "--data", str(data),
                 "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["method"] == "baseline" and report["rows"][0]["group"] == "ALL"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("printed, expected", [
    ("14/12/2026", date(2026, 12, 14)), ("04/01/2027", date(2027, 1, 4)), ("14 Dec 2026", date(2026, 12, 14)),
    ("17 August 2026", date(2026, 8, 17)), ("2026-12-14", date(2026, 12, 14)), ("l4/12/2026", date(2026, 12, 14)),
    ("1st Sept 2026", date(2026, 9, 1)), ("20Jul 2026", date(2026, 7, 20)), ("26Jul2026", date(2026, 7, 26)),
    ("05Mar2027", date(2027, 3, 5)), ("1420-12-20", None), ("13/13/2026", None), ("", None), (None, None),
])
def test_dates_parsed_day_first_in_code(printed, expected):
    assert parse_date(printed) == expected


def test_schema_has_no_date_format_constraint():
    # Ollama's grammar for format=date forced YYYY first and mangled day-first dates (14/12/2026 -> 1420-12-20)
    assert "format" not in json.dumps(json_schema())


def test_raw_output_is_kept_so_cached_runs_can_be_reparsed():
    raw = json.dumps({**{f: None for f in json_schema()["required"]}, "pay_date": "29Oct2026", "lines": []})

    class Fixed:
        name, model = "fake", "fake"

        def complete(self, system, messages, schema):
            return raw

    result = LlmExtractor(Fixed()).extract(DocText(lines=[], source="pdf_text"))
    assert result.final_raw == raw
    assert payslip_from_raw(result.final_raw).pay_date == date(2026, 10, 29)
