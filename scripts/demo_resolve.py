"""Resolve coverage for a few example payslips.

    python scripts/demo_resolve.py
"""

from wagecheck.coverage.identity import PayslipIdentity
from wagecheck.coverage.resolver import resolve

EXAMPLES = [
    PayslipIdentity(employer_name="Coles Supermarkets Australia Pty Ltd", abn="45 004 189 708",
                    job_title="Night fill", workplace_description="supermarket"),
    PayslipIdentity(employer_name="Harbour Fresh Grocers", job_title="Night fill",
                    workplace_description="independent supermarket"),
    PayslipIdentity(employer_name="Little Bean Cafe", job_title="Barista", workplace_description="cafe"),
]

for ident in EXAMPLES:
    r = resolve(ident)
    print(f"\n{ident.employer_name} / {ident.job_title}")
    print(f"  -> {r.instrument_type}: {r.title} [{r.instrument_id}]")
    print(f"     rate source: {r.rate_source}   confidence: {r.confidence}")
    for e in r.evidence:
        print(f"     evidence: {e}")
    for w in r.warnings:
        print(f"     warning:  {w}")
    if r.alternatives:
        print(f"     alternatives: {[a.code for a in r.alternatives]}")
