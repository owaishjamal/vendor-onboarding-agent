"""Render a submission's documents to real files the pipeline reads for real.

Most documents render as clean text PDFs (reliable text-layer extraction).
Any document whose spec sets `"scan": true` is rasterised to an image so the
reader is forced down its OCR path — the same happy-path-plus-one-scan pattern
the invoice build used, so document reading is demonstrably real and not a
lookup.

The layout is deliberately simple and labelled ("Name:", "Number:", "Issue
Date:") because the reader parses labelled fields. Real documents are messier;
a production reader pairs this with a vision model. The point being proven here
is the cross-referencing and the confidence handling, not layout-robust OCR.
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

W, H = A4

KIND_TITLE = {
    "w9": "FORM W-9  ·  Request for Taxpayer Identification Number",
    "bank_letter": "BANK CONFIRMATION LETTER",
    "bank_statement": "BANK STATEMENT",
    "voided_cheque": "VOIDED CHEQUE",
    "cancelled_cheque": "CANCELLED CHEQUE",
    "certificate_of_incorporation": "CERTIFICATE OF INCORPORATION",
    "companies_house_extract": "COMPANIES HOUSE — COMPANY EXTRACT",
    "handelsregisterauszug": "HANDELSREGISTERAUSZUG  (Commercial Register Extract)",
    "vat_certificate": "VAT REGISTRATION CERTIFICATE",
    "gst_certificate": "GST REGISTRATION CERTIFICATE",
    "acra_bizfile": "ACRA BUSINESS PROFILE",
    "delivery_note": "DELIVERY NOTE",
}

KIND_AUTHORITY = {
    "w9": "Internal Revenue Service",
    "bank_letter": "Issued by the account-holding bank",
    "voided_cheque": "Bank of record",
    "cancelled_cheque": "Bank of record",
    "certificate_of_incorporation": "Registrar of Companies",
    "companies_house_extract": "Companies House",
    "handelsregisterauszug": "Amtsgericht — Handelsregister",
    "vat_certificate": "Tax Authority",
    "gst_certificate": "Goods and Services Tax Network",
    "acra_bizfile": "Accounting and Corporate Regulatory Authority",
    "delivery_note": "Supplier despatch",
}


def _name_of(ex: dict) -> str | None:
    return ex.get("legal_name") or ex.get("account_name") or ex.get("name")


def _number_of(ex: dict) -> str | None:
    return (ex.get("registration_number") or ex.get("company_number")
            or ex.get("tax_id") or ex.get("vat_number") or ex.get("ein"))


def _render_pdf(ex: dict) -> bytes:
    kind = ex.get("kind", "")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    # Header band
    c.setFillColorRGB(0.12, 0.16, 0.23)
    c.rect(0, H - 28 * mm, W, 28 * mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, H - 18 * mm, KIND_TITLE.get(kind, kind.replace("_", " ").upper()))
    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, H - 24 * mm, KIND_AUTHORITY.get(kind, ""))

    c.setFillColorRGB(0, 0, 0)
    y = H - 45 * mm
    c.setFont("Helvetica", 11)

    def row(label: str, value: str) -> None:
        nonlocal y
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * mm, y, f"{label}:")
        c.setFont("Helvetica", 11)
        c.drawString(62 * mm, y, str(value))
        y -= 9 * mm

    name = _name_of(ex)
    number = _number_of(ex)
    # The bank documents name the ACCOUNT HOLDER; everything else names the entity.
    name_label = "Account Holder" if kind in (
        "bank_letter", "bank_statement", "voided_cheque", "cancelled_cheque") else "Name"
    if name:
        row(name_label, name)
    if number:
        num_label = ("Company Number" if "incorporation" in kind or "companies" in kind
                     or "acra" in kind or "handels" in kind else
                     "Tax / VAT Number" if kind in ("vat_certificate", "gst_certificate", "w9")
                     else "Reference Number")
        row(num_label, number)
    if ex.get("account_number"):
        row("Account Number", ex["account_number"])
    if ex.get("issue_date"):
        row("Issue Date", ex["issue_date"])
    if ex.get("expiry_date"):
        row("Expiry Date", ex["expiry_date"])

    y -= 6 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(20 * mm, y, "This document was generated for the onboarding case study and is not a real instrument.")

    c.showPage()
    c.save()
    return buf.getvalue()


def _rasterise(pdf_bytes: bytes, dpi: int = 160, rotate: float = 0.5) -> bytes:
    """Render the PDF to a slightly skewed greyscale PNG — i.e. a scan."""
    import fitz
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=dpi)
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    im = im.rotate(rotate, resample=Image.BICUBIC, fillcolor=255, expand=False)
    im = im.point(lambda p: min(255, int(p * 0.95 + 12)))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def render_document(ex: dict, filename: str, out_path: Path, scan: bool = False) -> str:
    """Render one document. Returns the actual on-disk filename (may change ext)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = _render_pdf(ex)
    if scan:
        stem = Path(filename).stem
        png = _rasterise(pdf)
        target = out_path.parent / f"{stem}.png"
        target.write_bytes(png)
        return target.name
    target = out_path.parent / Path(filename).with_suffix(".pdf").name
    target.write_bytes(pdf)
    return target.name
