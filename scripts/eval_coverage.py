"""Coverage-resolution accuracy on the labelled cases.

    python scripts/eval_coverage.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

from wagecheck.coverage.identity import PayslipIdentity
from wagecheck.coverage.resolver import resolve

CASES = Path(__file__).resolve().parents[1] / "evals" / "coverage_cases.yaml"


def identity_for(case: dict[str, str]) -> PayslipIdentity:
    return PayslipIdentity(
        employer_name=case.get("employer") or None,
        abn=case.get("abn"),
        job_title=case["job"],
        workplace_description=case["place"],
    )


def evaluate(split: str = "cases") -> dict[str, float]:
    cases = yaml.safe_load(CASES.read_text())[split]
    top1 = top3 = 0
    misses = []
    for c in cases:
        r = resolve(identity_for(c))
        ranked = [r.instrument_id] + [a.code for a in r.alternatives]
        if ranked[0] == c["expected"]:
            top1 += 1
        else:
            misses.append(f"  {c['job']} @ {c['place']}: expected {c['expected']}, got {ranked[:3]}")
        if c["expected"] in ranked[:3]:
            top3 += 1
    n = len(cases)
    print(f"[{split}] n={n}  top1={top1 / n:.1%}  top3={top3 / n:.1%}")
    print("\n".join(misses) or "  no misses")
    return {"n": n, "top1": top1 / n, "top3": top3 / n}


if __name__ == "__main__":
    evaluate("cases")
    evaluate("held_out")
