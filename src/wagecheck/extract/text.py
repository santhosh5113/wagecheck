"""Turn a payslip file into positioned text lines, from a digital PDF or a photo/scan.

Both paths produce the same shape: a list of lines, each a list of (x, text) tokens, top to bottom.
Scans are de-skewed using the angle of the OCR boxes before rows are rebuilt, because a 2-degree tilt
is enough to push a description onto a different row from its numbers.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

Source = Literal["pdf_text", "ocr"]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


@dataclass
class TextLine:
    y: float
    tokens: list[tuple[float, str]]

    @property
    def text(self) -> str:
        return " ".join(t for _, t in self.tokens)


@dataclass
class DocText:
    lines: list[TextLine]
    source: Source

    def as_text(self) -> str:
        """Plain text with wide gaps between columns, for prompting an LLM."""
        out = []
        for ln in self.lines:
            parts, last_x = [], None
            for x, t in ln.tokens:
                if last_x is not None:
                    parts.append("   " if x - last_x > 40 else " ")
                parts.append(t)
                last_x = x + 6 * len(t)
            out.append("".join(parts))
        return "\n".join(out)


def _group(items: list[tuple[float, float, str]], tol: float) -> list[TextLine]:
    """items: (x, y, text). Cluster into rows by y, then order tokens by x."""
    rows: list[list[tuple[float, float, str]]] = []
    for it in sorted(items, key=lambda t: t[1]):
        if rows and abs(statistics.fmean(r[1] for r in rows[-1]) - it[1]) <= tol:
            rows[-1].append(it)
        else:
            rows.append([it])
    return [TextLine(y=statistics.fmean(r[1] for r in row), tokens=sorted((r[0], r[2]) for r in row))
            for row in rows]


def from_pdf(path: Path) -> DocText:
    import pymupdf

    items: list[tuple[float, float, str]] = []
    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
        for page in doc:
            for x0, y0, _x1, y1, w, *_ in page.get_text("words"):
                items.append((x0, (y0 + y1) / 2 + page.number * 2000, w))
    return DocText(lines=_group(items, tol=3.0), source="pdf_text")


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]  # pip install wagecheck[ocr]

    return RapidOCR()


def from_image(path: Path) -> DocText:
    result, _ = _ocr_engine()(str(path))
    boxes = result or []
    if not boxes:
        return DocText(lines=[], source="ocr")
    angles, heights = [], []
    for box, _txt, _conf in boxes:
        (x0, y0), (x1, y1) = box[0], box[1]
        if x1 - x0 > 40:
            angles.append(math.atan2(y1 - y0, x1 - x0))
        heights.append(abs(box[3][1] - box[0][1]))
    theta = statistics.median(angles) if angles else 0.0
    cos, sin = math.cos(-theta), math.sin(-theta)
    items = []
    for box, txt, _conf in boxes:
        cx = sum(p[0] for p in box) / 4
        cy = sum(p[1] for p in box) / 4
        x_left = box[0][0]
        items.append((x_left * cos - box[0][1] * sin, cx * sin + cy * cos, str(txt)))
    tol = 0.5 * statistics.median(heights) if heights else 8.0
    return DocText(lines=_group(items, tol=tol), source="ocr")


def load(path: Path) -> DocText:
    """PDFs with a text layer are read directly; images and image-only PDFs go through OCR."""
    path = Path(path)
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return from_image(path)
    doc = from_pdf(path)
    if sum(len(ln.tokens) for ln in doc.lines) >= 10:
        return doc
    import pymupdf

    with pymupdf.open(path) as pdf:  # type: ignore[no-untyped-call]
        png = path.with_suffix(".ocr.png")
        pdf[0].get_pixmap(dpi=200).save(png)
    try:
        return from_image(png)
    finally:
        png.unlink(missing_ok=True)
