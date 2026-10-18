"""Render a synthetic payslip as a digital PDF and as a degraded "phone scan" image.

The three styles also differ visually (column order, fonts, where the totals sit), so a Phase 3
extractor can't just memorise one template.
"""

from __future__ import annotations

import io
import random
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from wagecheck.synth.payslip import Payslip

SYNTH_NOTE = "SYNTHETIC PAYSLIP - generated for testing WageCheck - not a real employer or employee"


def _money(x: Decimal) -> str:
    return f"${x:,.2f}"


def render_pdf(p: Payslip, path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    font, bold = {"codes": ("Courier", "Courier-Bold"), "descriptive": ("Helvetica", "Helvetica-Bold"),
                  "percent": ("Times-Roman", "Times-Bold")}[p.style]
    y = height - 50

    def text(x: float, s: str, f: str = font, size: int = 10) -> None:
        c.setFont(f, size)
        c.drawString(x, y, s)

    text(40, p.employer_name, bold, 14)
    y -= 16
    text(40, f"ABN {p.employer_abn}")
    text(380, "PAYSLIP", bold, 14)
    y -= 26
    text(40, f"Employee: {p.employee_name}")
    text(330, f"Employee ID: {p.employee_id}")
    y -= 14
    text(40, f"Position: {p.job_title} ({p.classification_label})")
    y -= 14
    text(40, f"Employment type: {p.employment_type}")
    if p.award_named:
        text(330, f"Award: {p.award_named}"[:48])
    y -= 14
    text(40, f"Pay period: {p.period_start:%d/%m/%Y} to {p.period_end:%d/%m/%Y}")
    text(330, f"Pay date: {p.pay_date:%d/%m/%Y}")
    y -= 30

    cols = {"codes": [("Code", 40), ("Description", 95), ("Hours", 330), ("Rate", 400), ("Amount", 480)],
            "descriptive": [("Description", 40), ("Units", 300), ("Rate", 370), ("Total", 470)],
            "percent": [("Pay item", 40), ("Rate", 320), ("Qty", 400), ("Amount", 480)]}[p.style]
    for name, x in cols:
        text(x, name, bold)
    y -= 6
    c.line(40, y, width - 40, y)
    y -= 14
    for ln in p.lines:
        values = {"Code": ln.code, "Description": ln.description, "Pay item": ln.description,
                  "Hours": f"{ln.hours}", "Units": f"{ln.hours} hrs", "Qty": f"{ln.hours}",
                  "Rate": _money(ln.rate), "Amount": _money(ln.amount), "Total": _money(ln.amount)}
        for name, x in cols:
            text(x, values[name])
        y -= 15
    y -= 6
    c.line(40, y, width - 40, y)
    y -= 18
    for name, val in (("Gross pay", p.gross), ("PAYG tax withheld", p.tax), ("Net pay", p.net),
                      ("Superannuation (12%)", p.super_amount)):
        text(300, name, bold if name in ("Gross pay", "Net pay") else font)
        text(470, _money(val), bold if name in ("Gross pay", "Net pay") else font)
        y -= 15
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(40, 30, SYNTH_NOTE)
    c.save()
    return path


def render_scan(pdf: Path, path: Path, rng: random.Random, dpi: int = 130) -> Path:
    """Rasterise and degrade: slight rotation, uneven lighting, blur, noise, JPEG artefacts."""
    import numpy as np
    import pymupdf
    from PIL import Image, ImageEnhance, ImageFilter

    with pymupdf.open(pdf) as doc:  # type: ignore[no-untyped-call]
        pix = doc[0].get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    img = img.rotate(rng.uniform(-2.0, 2.0), expand=True, fillcolor=235)
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.85, 1.0))
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
    arr = np.asarray(img, dtype=np.float32)
    arr += np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(3, 8), arr.shape)
    img = Image.fromarray(arr.clip(0, 255).astype("uint8"))
    img.save(path, format="JPEG", quality=rng.randint(45, 75))  # phone photos are JPEGs too
    return path
