"""Document Verification Agent tests.

The point of the DVA is that it classifies from CONTENT, so it generalises to
documents it has never seen. These tests use freshly-composed documents (not
the fixtures) to prove that.
"""

from __future__ import annotations

import io
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VO_DB_PATH", str(pathlib.Path(tempfile.gettempdir()) / "vo_dva.db"))
os.environ["CHECK_DELAY_MS"] = "0"

from backend.app.dva.classifier import classify_text  # noqa: E402
from backend.app.dva.preflight import preflight  # noqa: E402


# ===========================================================================
# Content classifier — no headings, just content
# ===========================================================================

def test_classifies_bank_document_by_content():
    text = ("We hereby confirm that the account holder is Acme Widgets Ltd. "
            "IBAN GB29 NWBK 6016 1331 9268 19, account number 31926819, sort code 60-16-13.")
    c = classify_text(text)
    assert c.detected_type == "bank_letter"
    assert not c.is_irrelevant


def test_classifies_vat_certificate_by_content():
    text = "Value Added Tax registration certificate. VAT number GB123456789 is active."
    assert classify_text(text).detected_type == "vat_certificate"


def test_classifies_incorporation_by_content():
    text = ("Certificate of Incorporation. This is to certify that ACME WIDGETS LTD is "
            "this day incorporated. Company number 09442817. Registrar of Companies.")
    assert classify_text(text).detected_type == "certificate_of_incorporation"


def test_flags_resume_as_irrelevant():
    text = ("Jane Doe — Curriculum Vitae. Work Experience: 5 years. Skills: Python, React. "
            "Education: BSc. References available on request.")
    c = classify_text(text)
    assert c.is_irrelevant
    assert "resume" in c.irrelevant_as.lower()
    assert c.detected_type is None


def test_flags_invoice_as_irrelevant():
    text = "Invoice number 4471. Invoice date 2026-01-05. Bill to Acme. Amount due 1200.00."
    assert classify_text(text).is_irrelevant


def test_unknown_content_is_not_forced_into_a_type():
    c = classify_text("The quick brown fox jumps over the lazy dog. Nothing to see here.")
    assert c.detected_type is None and not c.is_irrelevant


# ===========================================================================
# Preflight — the submission-time gate
# ===========================================================================

def _pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b, pagesize=A4)
    y = 780
    for ln in lines:
        c.drawString(60, y, ln)
        y -= 18
    c.showPage()
    c.save()
    return b.getvalue()


def test_preflight_rejects_resume_in_bank_slot():
    pdf = _pdf(["Jane Doe Curriculum Vitae", "Work Experience: Tesco",
                "Skills: Python", "Education: BTech"])
    v = preflight(pdf, "resume.pdf", "bank_proof",
                  accepted={"bank_letter", "bank_statement"}, legal_name="Acme Ltd")
    assert v["level"] == "error"
    assert v["status"] == "IRRELEVANT"


def test_preflight_accepts_real_bank_letter():
    pdf = _pdf(["BANK CONFIRMATION LETTER", "We hereby confirm the account holder: Acme Ltd",
                "IBAN GB29NWBK60161331926819", "Account number 31926819"])
    v = preflight(pdf, "bank.pdf", "bank_proof",
                  accepted={"bank_letter", "bank_statement"}, legal_name="Acme Ltd")
    assert v["level"] == "ok"
    assert v["detected_type"] == "bank_letter"


def test_preflight_flags_wrong_business_document():
    # A VAT certificate uploaded into the incorporation slot.
    pdf = _pdf(["VAT REGISTRATION CERTIFICATE", "VAT number GB123456789",
                "Value Added Tax", "is registered"])
    v = preflight(pdf, "vat.pdf", "incorporation",
                  accepted={"certificate_of_incorporation", "companies_house_extract"},
                  legal_name="Acme Ltd")
    assert v["level"] == "error"
    assert v["status"] == "WRONG_TYPE"
