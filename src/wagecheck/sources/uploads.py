"""Accept a document the user downloaded by hand, and check it is what we asked for.

A user might upload the wrong PDF (an older agreement, a different site's agreement, a pay
guide). Before anything is built from it, the upload is checked against the index record:
the agreement title should appear in the first pages of the document.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from wagecheck.coverage.identity import normalise_name
from wagecheck.sources.agreement_index import STOPWORDS, AgreementRecord


@dataclass(frozen=True)
class IngestedDocument:
    agreement_id: str
    stored_at: Path
    sha256: str
    pages: int
    title_match: float
    verified: bool
    message: str


def _first_pages_text(path: Path, pages: int = 3) -> tuple[str, int]:
    import pymupdf

    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
        text = " ".join(doc[i].get_text() for i in range(min(pages, doc.page_count)))
        return text, doc.page_count


def ingest_agreement(pdf: Path, record: AgreementRecord, store_dir: Path, min_match: float = 0.7
                     ) -> IngestedDocument:
    pdf = Path(pdf)
    if pdf.read_bytes()[:5] != b"%PDF-":
        raise ValueError(f"{pdf.name} is not a PDF")
    text, n_pages = _first_pages_text(pdf)
    doc_tokens = set(normalise_name(text).split())
    want = [t for t in normalise_name(record.title).split() if t not in STOPWORDS and len(t) > 1]
    match = sum(t in doc_tokens for t in want) / len(want) if want else 0.0
    id_seen = record.agreement_id.lower() in text.lower()
    verified = match >= min_match or id_seen

    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    dest = store_dir / f"{record.agreement_id}.pdf"
    shutil.copyfile(pdf, dest)
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    msg = (f"Matches {record.title} ({record.agreement_id})." if verified else
           f"This PDF doesn't look like {record.title}: only {match:.0%} of its title words appear in "
           "the first pages. Check you downloaded the right agreement.")
    return IngestedDocument(record.agreement_id, dest, sha, n_pages, round(match, 2), verified, msg)
