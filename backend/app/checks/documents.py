"""Do the documents corroborate the form, or just accompany it?

A vendor attaching a certificate of incorporation proves nothing by itself.
The question is whether the name and number ON that certificate match what
they typed into the form. A submission where the form says one company and the
attached bank letter says another is exactly the "subtle inconsistency" case —
and it is invisible unless you actually read the attachment and compare.

The document text is now READ FOR REAL. Each attachment is a file on disk; the
reader (see document_reader.py) opens it, uses the PDF text layer or falls back
to OCR for scans, and returns the name, number and dates found ON the document
plus a confidence. This check then compares that against the form.

Three things this makes real, all straight out of the problem statement:

  * "attach the wrong documents" — the reader DETECTS the document type from
    its content and we compare it to the type the vendor claimed. A delivery
    note submitted as a bank letter is caught.
  * subtle inconsistencies — the name/number on the actual document is
    compared to the form, so a certificate describing a different entity is
    surfaced.
  * we don't trust what we can't read — a document we need but read with low
    confidence is sent back to the vendor for a clearer copy, never approved
    on a guess.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from backend.app import config
from backend.app.checks.base import Timer, finding, name_score, name_verdict
from backend.app.checks.document_reader import MIN_READ_CONFIDENCE, read_document
from backend.app.models import (
    CheckResult, Finding, FindingCode, Severity, VendorSubmission,
)
from backend.app.rules import load_common_rules, required_documents

CHECK = "documents"


def _accepted_kinds_for(sub: VendorSubmission, doc_type: str) -> set[str]:
    """The document kinds acceptable for a given required-document slot."""
    for spec in required_documents(sub.country):
        if spec["doc_type"] == doc_type:
            return set(spec.get("accepted", []))
    return set()


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


def run(sub: VendorSubmission, today: Optional[date] = None) -> CheckResult:
    findings: list[Finding] = []
    today = today or date.today()
    doc_rules = load_common_rules().get("document_rules", {})
    max_age = doc_rules.get("max_age_months", 12)
    # Only documents attesting to a current state have a shelf life. See the
    # note in common.yaml - a certificate of incorporation never goes stale.
    freshness_required = set(doc_rules.get("freshness_required", []))
    per_doc: list[dict[str, Any]] = []

    with Timer() as t:
        for doc in sub.documents:
            read = read_document(doc)
            # Stamp the runtime read results back onto the doc so the UI and
            # other checks can see how it was read and how much to trust it.
            doc.detected_type = read.detected_type
            doc.read_confidence = round(read.confidence, 2)
            doc.read_source = read.source

            entry: dict[str, Any] = {
                "doc_type": doc.doc_type, "filename": doc.filename,
                "read_source": read.source, "confidence": round(read.confidence, 2),
                "detected_type": read.detected_type, "fields": read.fields,
                "note": read.note,
            }

            # --- could we read it at all?
            if read.source == "none" or read.confidence <= 0.01:
                findings.append(finding(
                    FindingCode.DOCUMENT_UNREADABLE, Severity.NEEDS_INFO, CHECK,
                    message=f"Document '{doc.filename}' could not be read. {read.note}",
                    field=f"documents.{doc.doc_type}",
                    vendor_message=(f"We couldn't read the file you attached "
                                    f"({doc.filename}). Please resend it as a clear PDF "
                                    f"or photo with all four corners visible."),
                    filename=doc.filename, note=read.note,
                ))
                per_doc.append(entry)
                continue

            # --- read, but not confidently enough to rely on
            if read.confidence < MIN_READ_CONFIDENCE:
                findings.append(finding(
                    FindingCode.DOCUMENT_LOW_CONFIDENCE, Severity.NEEDS_INFO, CHECK,
                    message=(f"'{doc.filename}' was read by {read.source} at only "
                             f"{read.confidence:.0%} confidence — too low to rely on for a "
                             f"decision."),
                    field=f"documents.{doc.doc_type}",
                    vendor_message=(f"The {doc.doc_type.replace('_', ' ')} you attached "
                                    f"was hard to read. Please resend a clearer scan or the "
                                    f"original PDF."),
                    filename=doc.filename, confidence=round(read.confidence, 2),
                    source=read.source,
                ))
                # Still attempt the comparisons below on a best-effort basis.

            # --- is it the type the vendor claimed? (catches wrong document)
            accepted = _accepted_kinds_for(sub, doc.doc_type)
            if read.detected_type and accepted and read.detected_type not in accepted:
                findings.append(finding(
                    FindingCode.DOCUMENT_TYPE_MISMATCH, Severity.NEEDS_INFO, CHECK,
                    message=(f"The file submitted as '{doc.doc_type}' reads as a "
                             f"'{read.detected_type.replace('_', ' ')}', which is not an "
                             f"accepted document for this slot ({sorted(accepted)})."),
                    field=f"documents.{doc.doc_type}",
                    vendor_message=(f"The file you attached for "
                                    f"{doc.doc_type.replace('_', ' ')} looks like a "
                                    f"{read.detected_type.replace('_', ' ')}. Please attach "
                                    f"the correct document."),
                    claimed=doc.doc_type, detected=read.detected_type,
                    accepted=sorted(accepted),
                ))

            # --- name on the document vs the name on the form
            doc_name = read.fields.get("name")
            if doc_name and sub.legal_name:
                score = name_score(sub.legal_name, doc_name)
                verdict = name_verdict(score)
                entry.update({"name_on_document": doc_name,
                              "name_score": round(score, 1), "verdict": verdict})
                # A bank document naming the account holder is expected to name a
                # person or the company's account — the account-holder mismatch is
                # owned by the consistency check, so only flag NON-bank documents
                # here to avoid double-counting.
                is_bank_doc = (read.detected_type or "").startswith(("bank", "cancelled", "voided"))
                if verdict == "MISMATCH" and not is_bank_doc:
                    findings.append(finding(
                        FindingCode.DOCUMENT_NAME_MISMATCH, Severity.NEEDS_REVIEW, CHECK,
                        message=(
                            f"The name on {doc.doc_type} ('{doc_name}') does not match the "
                            f"registered legal name on the form ('{sub.legal_name}') — "
                            f"{score:.0f}% similar. The supporting evidence describes a "
                            f"different entity from the one applying."
                        ),
                        field=f"documents.{doc.doc_type}",
                        document=doc.filename, name_on_document=doc_name,
                        name_on_form=sub.legal_name, score=round(score, 1),
                    ))

            # --- identifier on the document vs the form
            doc_number = read.fields.get("number")
            if doc_number:
                entry["number_on_document"] = doc_number
                form_numbers = {
                    (sub.registration_number or "").replace(" ", "").upper(),
                    (sub.tax_id or "").replace(" ", "").upper(),
                }
                form_numbers.discard("")
                norm = doc_number.replace(" ", "").upper()
                if form_numbers and norm not in form_numbers and read.confidence >= MIN_READ_CONFIDENCE:
                    findings.append(finding(
                        FindingCode.DOCUMENT_NAME_MISMATCH, Severity.ADVISORY, CHECK,
                        message=(f"Identifier '{doc_number}' on {doc.doc_type} does not appear "
                                 f"among the numbers given on the form "
                                 f"({', '.join(sorted(form_numbers))})."),
                        field=f"documents.{doc.doc_type}",
                        document=doc.filename, number_on_document=doc_number,
                        numbers_on_form=sorted(form_numbers),
                    ))

            # --- currency of the evidence
            issued = _parse_date(read.fields.get("issue_date"))
            expires = _parse_date(read.fields.get("expiry_date"))
            if expires and expires < today:
                entry["expired_on"] = str(expires)
                findings.append(finding(
                    FindingCode.DOCUMENT_EXPIRED, Severity.NEEDS_INFO, CHECK,
                    message=f"{doc.doc_type} expired on {expires}.",
                    field=f"documents.{doc.doc_type}",
                    vendor_message=(f"The {doc.doc_type.replace('_', ' ')} you provided "
                                    f"expired on {expires}. Please send a current one."),
                    document=doc.filename, expiry_date=str(expires),
                ))
            elif (issued and doc.doc_type in freshness_required
                  and _months_between(issued, today) > max_age):
                entry["issued"] = str(issued)
                findings.append(finding(
                    FindingCode.DOCUMENT_EXPIRED, Severity.NEEDS_INFO, CHECK,
                    message=(f"{doc.doc_type} was issued {_months_between(issued, today)} "
                             f"months ago, beyond the {max_age}-month limit."),
                    field=f"documents.{doc.doc_type}",
                    vendor_message=(f"The {doc.doc_type.replace('_', ' ')} you provided is "
                                    f"dated {issued}, which is older than we can accept. "
                                    f"Please send one issued in the last {max_age} months."),
                    document=doc.filename, issue_date=str(issued),
                ))

            per_doc.append(entry)

    summary = (f"{len(sub.documents)} document(s) checked; all corroborate the form."
               if not findings else
               f"{len(sub.documents)} document(s) checked; {len(findings)} issue(s) found.")
    if not sub.documents:
        summary = "No documents attached to verify."

    return CheckResult(check=CHECK, label="Document verification", findings=findings,
                       summary=summary, duration_ms=t.ms, data={"documents": per_doc})
