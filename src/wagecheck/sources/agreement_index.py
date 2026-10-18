"""Local, searchable index of approved enterprise agreements.

Built from the FWC's yearly "List of Agreements" PDFs, which the user downloads once.
Columns (as published in 2026): Agreement Title | Agreement ID | Version | Print ID |
Matter Number | Industry | Operative Date | Expiry Date. Titles wrap over several lines, so the
parser places every word in a column by its x position under the header row, and starts a
new record on each line whose Agreement ID cell holds an ID like AE531861.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from wagecheck.coverage.identity import normalise_name
from wagecheck.sources.catalog import agreement_pdf_url

COLUMNS = ["title", "agreement_id", "version", "print_id", "matter", "industry", "operative", "expiry"]
HEADER_LABELS = ["Agreement Title", "Agreement ID", "Version", "Print ID", "Matter Number", "Industry",
                 "Operative Date", "Expiry Date"]
ID_RE = re.compile(r"^A[A-Z]\d{6}$")
PRINT_RE = re.compile(r"^PR\d{6}$")
MATTER_RE = re.compile(r"^[A-Z]{1,3}\d{4}/\d+$")
SKIP_RE = re.compile(r"Publication Team|^Page \d+ of \d+$|^List of Agreements$|^From \d|^Report generated")
STOPWORDS = {
    "and", "the", "of", "agreement", "enterprise", "australia", "australian", "group", "services",
    "holdings", "trust", "trustee", "for", "t", "as", "nsw", "vic", "qld", "wa", "sa", "tas", "nt", "act",
}


@dataclass
class AgreementRecord:
    title: str
    agreement_id: str
    version: str = ""
    print_id: str = ""
    matter: str = ""
    industry: str = ""
    operative: date | None = None
    expiry: date | None = None
    source: str = ""
    problems: list[str] = field(default_factory=list)


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(" ".join(s.split()), "%d %B %Y").date()
    except ValueError:
        return None


def _header_columns(lines: list[tuple[float, list[tuple[float, str]]]]) -> tuple[float, list[float]] | None:
    """Find the column header line; return its y and the left x of each column."""
    for y, words in lines:
        texts = [w for _, w in words]
        if "Version" in texts and "Matter" in texts and "Expiry" in texts:
            xs: list[float] = []
            i = 0
            for label in HEADER_LABELS:
                parts = label.split()
                while i < len(words) and words[i][1] != parts[0]:
                    i += 1
                if i >= len(words):
                    return None
                xs.append(words[i][0])
                i += len(parts)
            return y, xs
    return None


def _lines(page: object) -> list[tuple[float, list[tuple[float, str]]]]:
    words = page.get_text("words")  # type: ignore[attr-defined]
    rows: list[tuple[float, list[tuple[float, str]]]] = []
    for x0, y0, _x1, _y1, w, *_ in sorted(words, key=lambda t: (round(t[1], 0), t[0])):
        if rows and abs(rows[-1][0] - y0) <= 2.5:
            rows[-1][1].append((x0, w))
        else:
            rows.append((y0, [(x0, w)]))
    return [(y, sorted(ws)) for y, ws in rows]


def parse_list_pdf(path: Path) -> tuple[list[AgreementRecord], list[str]]:
    import pymupdf  # imported lazily so the core package doesn't need it

    records: list[AgreementRecord] = []
    warnings: list[str] = []
    cols: list[float] | None = None
    current: dict[str, list[str]] | None = None

    def flush() -> None:
        if current is None:
            return
        cells = {k: " ".join(v).strip() for k, v in current.items()}
        rec = AgreementRecord(
            title=" ".join(cells.get("title", "").split()),
            agreement_id=cells.get("agreement_id", ""),
            version=cells.get("version", ""),
            print_id=cells.get("print_id", ""),
            matter=cells.get("matter", ""),
            industry=cells.get("industry", ""),
            operative=_parse_date(cells.get("operative", "")),
            expiry=_parse_date(cells.get("expiry", "")),
            source=path.name,
        )
        for name, rx in (("agreement_id", ID_RE), ("print_id", PRINT_RE), ("matter", MATTER_RE)):
            if not rx.match(getattr(rec, name)):
                rec.problems.append(f"{name}={getattr(rec, name)!r}")
        if not rec.title:
            rec.problems.append("empty title")
        if rec.problems:
            warnings.append(f"{path.name}: {rec.agreement_id or '?'}: " + ", ".join(rec.problems))
        records.append(rec)

    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
        for page in doc:
            lines = _lines(page)
            found = _header_columns(lines)
            header_y = -1.0
            if found:
                header_y, cols = found
            if cols is None:
                warnings.append(f"{path.name} p{page.number + 1}: no column header found")
                continue
            for y, words in lines:
                if y <= header_y + 1:
                    continue
                if SKIP_RE.search(" ".join(w for _, w in words)):
                    continue
                cells: dict[str, list[str]] = {}
                for x, w in words:
                    idx = max((i for i, cx in enumerate(cols) if x >= cx - 2), default=0)
                    cells.setdefault(COLUMNS[idx], []).append(w)
                if ID_RE.match(" ".join(cells.get("agreement_id", []))):
                    flush()
                    current = cells
                elif current is not None:
                    for k, v in cells.items():
                        current.setdefault(k, []).extend(v)
    flush()
    return records, warnings


# ---------------- index ----------------

@dataclass(frozen=True)
class IndexHit:
    agreement_id: str
    title: str
    industry: str
    operative: date | None
    expiry: date | None
    matter: str
    match: float  # share of the employer-name tokens found in the title
    url: str
    note: str = ""


def _tokens(name: str) -> list[str]:
    return [t for t in normalise_name(name).split() if t not in STOPWORDS and len(t) > 1]


class AgreementIndex:
    def __init__(self, db_path: Path) -> None:
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS agreements (
                agreement_id TEXT PRIMARY KEY, title TEXT, version TEXT, print_id TEXT, matter TEXT,
                industry TEXT, operative TEXT, expiry TEXT, source TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS agreements_fts USING fts5(
                agreement_id UNINDEXED, title, industry, tokenize='porter unicode61');
            """
        )

    def add(self, records: list[AgreementRecord]) -> int:
        n = 0
        with self.db:
            for r in records:
                if not ID_RE.match(r.agreement_id):
                    continue
                self.db.execute("DELETE FROM agreements_fts WHERE agreement_id = ?", (r.agreement_id,))
                self.db.execute(
                    "INSERT OR REPLACE INTO agreements VALUES (?,?,?,?,?,?,?,?,?)",
                    (r.agreement_id, r.title, r.version, r.print_id, r.matter, r.industry,
                     r.operative.isoformat() if r.operative else None,
                     r.expiry.isoformat() if r.expiry else None, r.source),
                )
                self.db.execute("INSERT INTO agreements_fts VALUES (?,?,?)",
                                (r.agreement_id, r.title, r.industry))
                n += 1
        return n

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM agreements").fetchone()[0])

    def search(self, names: list[str], limit: int = 5, today: date | None = None) -> list[IndexHit]:
        today = today or date.today()
        best: dict[str, IndexHit] = {}
        for name in names:
            toks = _tokens(name)
            if not toks:
                continue
            query = " OR ".join(f'"{t}"' for t in toks)
            rows = self.db.execute(
                "SELECT a.agreement_id, a.title, a.industry, a.operative, a.expiry, a.matter "
                "FROM agreements_fts f JOIN agreements a USING (agreement_id) "
                "WHERE agreements_fts MATCH ? ORDER BY bm25(agreements_fts) LIMIT 50",
                (query,),
            ).fetchall()
            for aid, title, industry, op, ex, matter in rows:
                title_toks = set(normalise_name(title).split())
                match = sum(t in title_toks for t in toks) / len(toks)
                if match < 0.5:
                    continue
                exp = date.fromisoformat(ex) if ex else None
                note = ""
                if exp and exp < today:
                    note = ("Past its nominal expiry date; agreements keep operating until replaced "
                            "or terminated, so check for a newer one.")
                hit = IndexHit(aid, title, industry, date.fromisoformat(op) if op else None, exp, matter,
                               round(match, 2), agreement_pdf_url(aid), note)
                if aid not in best or hit.match > best[aid].match:
                    best[aid] = hit
        ranked = sorted(best.values(), key=lambda h: (h.match, h.operative or date.min), reverse=True)
        return ranked[:limit]

    def close(self) -> None:
        self.db.close()
