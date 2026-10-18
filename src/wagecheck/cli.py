"""Command line entry point.

    wagecheck index build data/fwc_lists          # parse yearly List of Agreements PDFs
    wagecheck index search "Coles Supermarkets"   # find candidate agreements
    wagecheck links lists 2024 2025 2026          # official links to download the yearly lists
    wagecheck fetch award MA000003                # award text, if robots.txt allows
    wagecheck agreement add AE524516 ~/Downloads/ae524516.pdf
    wagecheck mapd check MA000004                 # compare a rule pack with live MAPD rates
    wagecheck mapd penalties MA000003 --level 1   # MAPD penalty $ values, for building packs
    wagecheck mapd allowances MA000119            # wage and expense allowances in force
    wagecheck synth --n 200 --scans               # synthetic payslips with injected underpayments
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from wagecheck.mapd.client import (
    MapdApiError,
    MapdClient,
    cross_check,
    current_allowances,
    current_hourly_rates,
    current_penalties,
)
from wagecheck.rules.loader import load_pack
from wagecheck.sources import catalog
from wagecheck.sources.agreement_index import AgreementIndex, AgreementRecord, parse_list_pdf
from wagecheck.sources.fetcher import NeedsUser, PoliteFetcher
from wagecheck.sources.uploads import ingest_agreement

DATA = Path("data")


def _index(args: argparse.Namespace) -> AgreementIndex:
    return AgreementIndex(Path(args.db))


def cmd_index_build(args: argparse.Namespace) -> int:
    idx = _index(args)
    pdfs = sorted(Path(args.folder).glob("agreements*.pdf"))
    if not pdfs:
        print(f"No agreements<year>.pdf files in {args.folder}. Run `wagecheck links lists` first.")
        return 1
    for pdf in pdfs:
        records, warnings = parse_list_pdf(pdf)
        added = idx.add(records)
        print(f"{pdf.name}: {added} agreements indexed, {len(warnings)} warnings")
        for w in warnings[:10]:
            print("  !", w)
    print(f"Index now holds {idx.count()} agreements.")
    return 0


def cmd_index_search(args: argparse.Namespace) -> int:
    hits = _index(args).search([args.name], limit=args.limit)
    if not hits:
        print(f"No match. Try the trading name or legal name, or search {catalog.DOCUMENT_SEARCH}")
        return 1
    for h in hits:
        print(f"{h.agreement_id}  match {h.match:.0%}  {h.title}")
        print(f"    {h.industry} | operative {h.operative} | expiry {h.expiry}")
        print(f"    download: {h.url}")
        if h.note:
            print(f"    note: {h.note}")
    return 0


def cmd_links_lists(args: argparse.Namespace) -> int:
    print("Download these in your browser and save them into data/fwc_lists/:")
    for y in args.years:
        print(" ", catalog.agreement_list_url(y))
    return 0


def cmd_fetch_award(args: argparse.Namespace) -> int:
    f = PoliteFetcher(DATA / "cache")
    res = catalog.fetch_award_text(f, args.code)
    if isinstance(res, NeedsUser):
        print(f"Not downloaded: {res.reason}.\n{res.instructions}\n  {res.url}")
        return 2
    print(f"Saved {res.path} (sha256 {res.sha256[:12]}…, {'cached' if res.from_cache else 'fresh'})")
    return 0


def cmd_agreement_add(args: argparse.Namespace) -> int:
    idx = _index(args)
    row = idx.db.execute("SELECT title, agreement_id FROM agreements WHERE agreement_id = ?",
                         (args.agreement_id.upper(),)).fetchone()
    if row is None:
        print(f"{args.agreement_id} is not in the index. Build the index first.")
        return 1
    rec = AgreementRecord(title=row[0], agreement_id=row[1])
    doc = ingest_agreement(Path(args.pdf).expanduser(), rec, DATA / "agreements")
    print(("OK: " if doc.verified else "CHECK: ") + doc.message)
    return 0 if doc.verified else 3


def _on(args: argparse.Namespace) -> date:
    return date.fromisoformat(args.on) if args.on else date.today()


def cmd_mapd_check(args: argparse.Namespace) -> int:
    on = _on(args)
    with MapdClient(cache_dir=DATA / "mapd_cache") as c:
        records = c.pay_rates(args.code)
    print(f"{len(records)} pay-rate records for {args.code} (all years)")
    print("employee_rate_type_code values:", dict(Counter(r.get("employee_rate_type_code") for r in records)))
    print("calculated_rate_type values:  ", dict(Counter(r.get("calculated_rate_type") for r in records)))
    rates = current_hourly_rates(records, on, args.rate_type)
    print(f"\nHourly '{args.rate_type}' rates in force on {on}:")
    for r in sorted(rates, key=lambda r: (r.level or 0, r.classification)):
        print(f"  level {r.level} id {r.fixed_id}: {r.rate:>8}  {r.classification}  [parent: {r.parent}]"
              f"  ({r.operative_from} to {r.operative_to})")
    try:
        pack = load_pack(args.code, on)
    except LookupError:
        print(f"\nNo local rule pack for {args.code}; nothing to compare.")
        return 0
    diffs = cross_check(pack, rates)
    print(f"\nRule pack {pack.instrument_id} effective {pack.effective_from}: "
          + ("matches MAPD" if not diffs else f"{len(diffs)} difference(s)"))
    for d in diffs:
        print("  !", d)
    return 0 if not diffs else 4


def cmd_mapd_penalties(args: argparse.Namespace) -> int:
    on = _on(args)
    with MapdClient(cache_dir=DATA / "mapd_cache") as c:
        records = c.penalties(args.code, args.level, args.rate_type)
        rows = current_penalties(records, on, args.level, args.rate_type)
    for r in rows:
        print(f"L{r['classification_level']} {r['employee_rate_type_code']:<4} {r['rate']!s:>6}% "
              f"${r['penalty_calculated_value']!s:>8}  {r['penalty_description']}  [{r['clause_description']}]")
    print(f"{len(rows)} penalty rows in force on {on}")
    return 0


def cmd_mapd_allowances(args: argparse.Namespace) -> int:
    on = _on(args)
    with MapdClient(cache_dir=DATA / "mapd_cache") as c:
        wage = current_allowances(c.wage_allowances(args.code), on)
        expense = current_allowances(c.expense_allowances(args.code), on)
    for kind, rows in (("WAGE", wage), ("EXPENSE", expense)):
        print(f"--- {kind} allowances in force on {on}: {len(rows)}")
        for r in rows:
            print(f"  ${r['allowance_amount']!s:>8} per {r['payment_frequency']}  {r['allowance']}"
                  f"  [cl {r['clauses']}]")
    return 0


def cmd_synth(args: argparse.Namespace) -> int:
    import csv
    import json
    import random as _random

    from wagecheck.synth.generate import check_sample, generate
    from wagecheck.synth.render import render_pdf, render_scan

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    samples = generate(args.n, args.seed)
    bad = 0
    with open(out / "manifest.jsonl", "w") as manifest:
        for smp in samples:
            problems = check_sample(smp)
            bad += bool(problems)
            d = out / smp.id
            d.mkdir(exist_ok=True)
            (d / "truth.json").write_text(smp.model_dump_json(indent=2))
            with open(d / "roster.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "start", "end", "unpaid_break_minutes"])
                for sh in smp.shifts:
                    brk = sum(int((b.end - b.start).total_seconds() // 60) for b in sh.unpaid_breaks)
                    w.writerow([sh.start.date(), sh.start.strftime("%H:%M"), sh.end.strftime("%H:%M"), brk])
            pdf = render_pdf(smp.payslip, d / "payslip.pdf")
            if args.scans:
                render_scan(pdf, d / "payslip_scan.jpg", _random.Random(f"{args.seed}-{smp.id}"))
            manifest.write(json.dumps({"id": smp.id, "scenario": smp.scenario, "award": smp.award,
                                       "style": smp.payslip.style, "errors": [e.kind for e in smp.errors],
                                       "shortfall": str(smp.shortfall)}) + "\n")
    kinds = Counter(e.kind for smp in samples for e in smp.errors)
    clean = sum(not smp.errors for smp in samples)
    short = sum((smp.shortfall for smp in samples), Decimal(0))
    print(f"{len(samples)} samples written to {out} ({bad} failed invariant checks)")
    print(f"  clean payslips: {clean}   underpaid: {len(samples) - clean}   total shortfall: ${short:,.2f}")
    for kind, n in kinds.most_common():
        print(f"  {kind:<26} {n}")
    return 0 if bad == 0 else 6


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="wagecheck")
    p.add_argument("--db", default=str(DATA / "agreements.sqlite"))
    sub = p.add_subparsers(required=True)

    idx = sub.add_parser("index").add_subparsers(required=True)
    b = idx.add_parser("build")
    b.add_argument("folder", nargs="?", default=str(DATA / "fwc_lists"))
    b.set_defaults(fn=cmd_index_build)
    s = idx.add_parser("search")
    s.add_argument("name")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_index_search)

    links = sub.add_parser("links").add_subparsers(required=True)
    lists = links.add_parser("lists")
    lists.add_argument("years", nargs="+", type=int)
    lists.set_defaults(fn=cmd_links_lists)

    fetch = sub.add_parser("fetch").add_subparsers(required=True)
    fa = fetch.add_parser("award")
    fa.add_argument("code")
    fa.set_defaults(fn=cmd_fetch_award)

    ag = sub.add_parser("agreement").add_subparsers(required=True)
    add = ag.add_parser("add")
    add.add_argument("agreement_id")
    add.add_argument("pdf")
    add.set_defaults(fn=cmd_agreement_add)

    mapd = sub.add_parser("mapd").add_subparsers(required=True)
    mc = mapd.add_parser("check")
    mc.add_argument("code")
    mc.add_argument("--on", help="date YYYY-MM-DD (default today)")
    mc.add_argument("--rate-type", default="AD", help="employee_rate_type_code (default AD = adult)")
    mc.set_defaults(fn=cmd_mapd_check)
    mpen = mapd.add_parser("penalties")
    mpen.add_argument("code")
    mpen.add_argument("--level", type=int)
    mpen.add_argument("--on")
    mpen.add_argument("--rate-type", default=None)
    mpen.set_defaults(fn=cmd_mapd_penalties)

    mal = mapd.add_parser("allowances")
    mal.add_argument("code")
    mal.add_argument("--on")
    mal.set_defaults(fn=cmd_mapd_allowances)

    sy = sub.add_parser("synth", help="generate synthetic payslips with known underpayments")
    sy.add_argument("--n", type=int, default=200)
    sy.add_argument("--seed", type=int, default=42)
    sy.add_argument("--out", default=str(DATA / "synth"))
    sy.add_argument("--scans", action="store_true", help="also write a degraded scan image per payslip")
    sy.set_defaults(fn=cmd_synth)

    args = p.parse_args(argv)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    try:
        return int(args.fn(args))
    except MapdApiError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
