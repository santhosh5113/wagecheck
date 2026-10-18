"""Build a small PDF laid out like the FWC yearly "List of Agreements" report.

Rows are copied from the published 2026 list (public data); the Coles row is test data with the
shape of a real row. Layout: header block, 8 columns, titles wrapping onto following lines,
footer with page numbers, header repeated on page 2.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

COLS = [30, 300, 365, 410, 470, 545, 665, 740]
HEADERS = ["Agreement Title", "Agreement ID", "Version", "Print ID", "Matter Number", "Industry",
           "Operative Date", "Expiry Date"]

PAGE1 = [
    (["5B Adelaide Manufacturing Operations Agreement", "2025"], "AE531861", "1", "PR796165",
     "AG2025/4363", ["Manufacturing and", "associated industries"], "04 February 2026", "26 November 2028"),
    (["ALDI Jandakot Agreement 2025"], "AE531723", "1", "PR795445", "AG2025/1118", ["Retail industry"],
     "09 January 2026", "02 January 2030"),
    (["ALDI Prestons Agreement 2024"], "AE531722", "1", "PR795445", "AG2025/1118", ["Retail industry"],
     "09 January 2026", "02 January 2030"),
    (["ALL MELBOURNE CONSTRUCTIONS (VIC) TRUST (THE", "TRUSTEE FOR) and the CFMEU (Victorian",
      "Construction and General Division) Subcontractors", "Carpentry & Joinery Enterprise Agreement 2024 -",
      "2027"], "AE532224", "1", "PR797374", "AG2026/274",
     ["Building, metal and civil", "construction industries"],
     "12 March 2026", "02 July 2027"),
]
PAGE2 = [
    (["Coles Retail Enterprise Agreement 2024"], "AE524516", "1", "PR778001", "AG2024/1124",
     ["Retail industry"], "07 October 2024", "30 June 2027"),
    (["Old Corner Store Agreement 2019"], "AE500001", "1", "PR700001", "AG2019/1",
     ["Retail industry"], "01 March 2019", "01 March 2022"),
]


def build(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    _, height = landscape(A4)
    for n, rows in enumerate((PAGE1, PAGE2), start=1):
        y = height - 40
        if n > 1:
            for line in ("List of Agreements", "From 1 January 2026 to 30 March 2026",
                         "Report generated on 30 March 2026"):
                c.setFont("Helvetica", 9)
                c.drawString(30, y, line)
                y -= 12
            y -= 6
        c.setFont("Helvetica-Bold", 8)
        for x, h in zip(COLS, HEADERS, strict=True):
            c.drawString(x, y, h)
        y -= 16
        c.setFont("Helvetica", 8)
        for title, aid, ver, pr, matter, industry, op, ex in rows:
            for x, v in zip(COLS[1:5], (aid, ver, pr, matter), strict=True):
                c.drawString(x, y, v)
            c.drawString(COLS[6], y, op)
            c.drawString(COLS[7], y, ex)
            for i, line in enumerate(title):
                c.drawString(COLS[0], y - 10 * i, line)
            for i, line in enumerate(industry):
                c.drawString(COLS[5], y - 10 * i, line)
            y -= 10 * max(len(title), len(industry)) + 6
        c.setFont("Helvetica", 8)
        c.drawString(30, 30, "Publication Team—Email publications@fwc.gov.au")
        c.drawString(720, 30, f"Page {n} of 2")
        c.showPage()
    c.save()
    return path
