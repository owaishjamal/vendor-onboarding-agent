"""Document Verification Agent (DVA).

Verifies one document end to end and returns a verdict — the same shape as the
GrabHack merchant-onboarding DVA: read → classify → cross-reference → check
authenticity/consistency → decide. Every step contributes findings; the
overall verdict is the most serious thing found.

It answers three questions about a document, in order:

  1. RELEVANCE — is this even the right kind of document? A CV in a bank-proof
     slot is caught here, from content, before anything else.
  2. CONSISTENCY — does the name / number ON the document match the form? A
     certificate describing a different company is caught here.
  3. AUTHENTICITY / CURRENCY — is it readable, in date, internally coherent?

The heavy lifting (classification, extraction) runs offline by default and via
a vision model when configured — but the verdict logic below is identical
either way, which is the point of keeping it separate from extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from backend.app.checks.base import name_score, name_verdict
from backend.app.checks.document_reader import MIN_READ_CONFIDENCE, read_document
from backend.app.dva.classifier import classify_text
from backend.app.models import Finding, FindingCode, Severity


# What each finding means for the document's own verdict.
class DocStatus:
    VERIFIED = "VERIFIED"          # right type, corroborates the form
    NEEDS_INFO = "NEEDS_INFO"      # vendor-fixable: wrong/irrelevant/unreadable
    NEEDS_REVIEW = "NEEDS_REVIEW"  # internal: contradicts the form


@dataclass
class DocumentVerdict:
    doc_type: str                       # slot the vendor put it in
    filename: str
    status: str = DocStatus.VERIFIED
    detected_type: Optional[str] = None
    irrelevant_as: Optional[str] = None
    read_source: str = "none"
    read_confidence: float = 0.0
    classify_confidence: float = 0.0
    name_on_document: Optional[str] = None
    name_score: Optional[float] = None
    reasons: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type, "filename": self.filename,
            "status": self.status, "detected_type": self.detected_type,
            "irrelevant_as": self.irrelevant_as, "read_source": self.read_source,
            "read_confidence": round(self.read_confidence, 2),
            "classify_confidence": round(self.classify_confidence, 2),
            "name_on_document": self.name_on_document,
            "name_score": self.name_score, "reasons": self.reasons,
        }


def _f(code, severity, check, message, field_, vendor_message=None, **ev):
    return Finding(code=code, severity=severity, check=check, field=field_,
                   message=message, vendor_message=vendor_message, evidence=ev)


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(s), fmt).date()
        except ValueError:
            continue
    return None


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def verify(doc, sub, accepted: set[str], *, freshness_required: set[str],
           max_age_months: int, today: Optional[date] = None) -> DocumentVerdict:
    """Verify one document against the submission. Returns a verdict + findings."""
    today = today or date.today()
    check = "documents"
    slot_label = doc.doc_type.replace("_", " ")
    v = DocumentVerdict(doc_type=doc.doc_type, filename=doc.filename)

    read = read_document(doc)
    v.read_source = read.source
    v.read_confidence = read.confidence
    # Stamp back onto the doc for the UI / other checks.
    doc.detected_type = read.detected_type
    doc.read_confidence = round(read.confidence, 2)
    doc.read_source = read.source

    # ---- 0. could we read it at all? -------------------------------------
    if read.source == "none" or read.confidence <= 0.01:
        v.status = DocStatus.NEEDS_INFO
        v.reasons.append(read.note or "Could not read the document.")
        v.findings.append(_f(
            FindingCode.DOCUMENT_UNREADABLE, Severity.NEEDS_INFO, check,
            f"'{doc.filename}' could not be read. {read.note}",
            f"documents.{doc.doc_type}",
            vendor_message=(f"We couldn't read the file you attached ({doc.filename}). "
                            f"Please resend it as a clear PDF or photo."),
            filename=doc.filename))
        return v

    # ---- 1. RELEVANCE: what is this document, really? --------------------
    if read.source in ("vision", "provided") and read.detected_type:
        # Vision: trust the model's classification. Provided: the caller gave a
        # pre-parsed block (pasted JSON with no file), so its declared kind is
        # what we have — classifying empty text would be wrong.
        v.detected_type = read.detected_type
        v.classify_confidence = read.confidence
    else:
        cls = classify_text(read.raw_text or "")
        v.detected_type = cls.detected_type
        v.irrelevant_as = cls.irrelevant_as
        v.classify_confidence = cls.confidence
        v.reasons += cls.reasons

    if v.irrelevant_as:
        v.status = DocStatus.NEEDS_INFO
        v.findings.append(_f(
            FindingCode.DOCUMENT_TYPE_MISMATCH, Severity.NEEDS_INFO, check,
            (f"The file submitted as '{doc.doc_type}' is not a business document — "
             f"it reads like a {v.irrelevant_as}. It is not a {' or '.join(sorted(accepted)) or 'valid document'}."),
            f"documents.{doc.doc_type}",
            vendor_message=(f"The file you attached for {slot_label} looks like a "
                            f"{v.irrelevant_as}, not the document we need. Please attach a "
                            f"valid {slot_label}."),
            detected=v.irrelevant_as, claimed=doc.doc_type))
        return v

    if accepted and v.detected_type and v.detected_type not in accepted:
        v.status = DocStatus.NEEDS_INFO
        v.findings.append(_f(
            FindingCode.DOCUMENT_TYPE_MISMATCH, Severity.NEEDS_INFO, check,
            (f"The file submitted as '{doc.doc_type}' reads as a "
             f"'{v.detected_type.replace('_', ' ')}', not one of {sorted(accepted)}."),
            f"documents.{doc.doc_type}",
            vendor_message=(f"The file you attached for {slot_label} looks like a "
                            f"{v.detected_type.replace('_', ' ')}. Please attach the correct document."),
            detected=v.detected_type, claimed=doc.doc_type, accepted=sorted(accepted)))
        return v

    if accepted and v.detected_type is None:
        # Read fine, but couldn't confirm it's the document it claims to be.
        v.status = DocStatus.NEEDS_INFO
        v.findings.append(_f(
            FindingCode.DOCUMENT_TYPE_MISMATCH, Severity.NEEDS_INFO, check,
            (f"The file submitted as '{doc.doc_type}' could not be confirmed to be a "
             f"{' or '.join(sorted(accepted))} — its content didn't match that document type."),
            f"documents.{doc.doc_type}",
            vendor_message=(f"We couldn't confirm the file you attached for {slot_label} is "
                            f"the right document. Please attach a clear copy of the correct one."),
            claimed=doc.doc_type, accepted=sorted(accepted)))
        return v

    type_confirmed = bool(accepted and v.detected_type in accepted)

    # ---- 2. low-confidence read (type ok, but shaky) ---------------------
    if read.confidence < MIN_READ_CONFIDENCE:
        v.status = DocStatus.NEEDS_INFO
        v.findings.append(_f(
            FindingCode.DOCUMENT_LOW_CONFIDENCE, Severity.NEEDS_INFO, check,
            f"'{doc.filename}' was read at only {read.confidence:.0%} confidence.",
            f"documents.{doc.doc_type}",
            vendor_message=(f"The {slot_label} you attached was hard to read. Please resend "
                            f"a clearer scan or the original PDF."),
            confidence=round(read.confidence, 2)))

    # ---- 3. CONSISTENCY: does it describe THIS company? ------------------
    doc_name = read.fields.get("name")
    is_bank_doc = (v.detected_type or "").startswith(("bank", "cancelled", "voided"))
    if doc_name and sub.legal_name and not is_bank_doc:
        score = name_score(sub.legal_name, doc_name)
        v.name_on_document = doc_name
        v.name_score = round(score, 1)
        if name_verdict(score) == "MISMATCH":
            v.status = DocStatus.NEEDS_REVIEW
            v.findings.append(_f(
                FindingCode.DOCUMENT_NAME_MISMATCH, Severity.NEEDS_REVIEW, check,
                (f"The name on {doc.doc_type} ('{doc_name}') does not match the registered "
                 f"legal name '{sub.legal_name}' ({score:.0f}% similar) — the evidence "
                 f"describes a different entity."),
                f"documents.{doc.doc_type}",
                name_on_document=doc_name, name_on_form=sub.legal_name, score=round(score, 1)))

    # identifier cross-check (advisory) — skip bank documents, whose "number"
    # is an account number, not a registration or tax ID.
    doc_number = read.fields.get("number")
    if doc_number and not is_bank_doc and read.confidence >= MIN_READ_CONFIDENCE:
        form_numbers = {(sub.registration_number or "").replace(" ", "").upper(),
                        (sub.tax_id or "").replace(" ", "").upper()}
        form_numbers.discard("")
        if form_numbers and doc_number.replace(" ", "").upper() not in form_numbers:
            v.findings.append(_f(
                FindingCode.DOCUMENT_NAME_MISMATCH, Severity.ADVISORY, check,
                (f"Identifier '{doc_number}' on {doc.doc_type} is not among the numbers on "
                 f"the form ({', '.join(sorted(form_numbers))})."),
                f"documents.{doc.doc_type}",
                number_on_document=doc_number, numbers_on_form=sorted(form_numbers)))

    # ---- 4. CURRENCY: expiry / staleness --------------------------------
    expires = _parse_date(read.fields.get("expiry_date"))
    issued = _parse_date(read.fields.get("issue_date"))
    if expires and expires < today:
        v.status = v.status if v.status == DocStatus.NEEDS_REVIEW else DocStatus.NEEDS_INFO
        v.findings.append(_f(
            FindingCode.DOCUMENT_EXPIRED, Severity.NEEDS_INFO, check,
            f"{doc.doc_type} expired on {expires}.", f"documents.{doc.doc_type}",
            vendor_message=f"The {slot_label} you provided expired on {expires}. Please send a current one.",
            expiry_date=str(expires)))
    elif issued and doc.doc_type in freshness_required and _months_between(issued, today) > max_age_months:
        v.status = v.status if v.status == DocStatus.NEEDS_REVIEW else DocStatus.NEEDS_INFO
        v.findings.append(_f(
            FindingCode.DOCUMENT_EXPIRED, Severity.NEEDS_INFO, check,
            f"{doc.doc_type} is dated {issued}, older than the {max_age_months}-month limit.",
            f"documents.{doc.doc_type}",
            vendor_message=(f"The {slot_label} you provided is dated {issued}, which is older "
                            f"than we can accept. Please send one from the last {max_age_months} months."),
            issue_date=str(issued)))

    if type_confirmed and v.status == DocStatus.VERIFIED:
        v.reasons.append("Type confirmed and corroborates the form.")
    return v
