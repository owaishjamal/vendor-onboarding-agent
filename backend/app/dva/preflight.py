"""Preflight — verify a single document the instant it's attached.

Same agent, run on one file before the vendor even submits. It answers the one
question that saves a round trip: "is this the right kind of document?" — so the
form can flag "that looks like a resume, not a bank letter" immediately, rather
than after a full submission.

It classifies from content and (if a legal name is supplied) checks the name on
the document against it. It deliberately does NOT make an onboarding decision —
it's a fast, friendly gate at the point of upload.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Optional

from backend.app.checks.document_reader import MIN_READ_CONFIDENCE, read_document
from backend.app.checks.base import name_score, name_verdict
from backend.app.dva.classifier import classify_text
from backend.app.models import SubmittedDocument


def preflight(file_bytes: bytes, filename: str, doc_type: str,
              accepted: set[str], legal_name: Optional[str] = None) -> dict[str, Any]:
    """Return a quick verdict for one uploaded file. No persistence."""
    suffix = Path(filename).suffix.lower() or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        doc = SubmittedDocument(doc_type=doc_type, filename=filename)
        # Read directly from the temp file (bypass the data/documents root).
        read = _read_tmp(doc, tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    if read.source == "none" or read.confidence <= 0.01:
        return _verdict("UNREADABLE", "error",
                        f"We couldn't read {filename}. Please attach a clear PDF or photo.",
                        detected=None, filename=filename)

    if read.source == "vision" and read.detected_type:
        detected, irrelevant, cconf, reasons = read.detected_type, None, read.confidence, []
    else:
        cls = classify_text(read.raw_text or "")
        detected, irrelevant, cconf, reasons = (
            cls.detected_type, cls.irrelevant_as, cls.confidence, cls.reasons)

    label = doc_type.replace("_", " ")

    if irrelevant:
        return _verdict("IRRELEVANT", "error",
                        f"This looks like a {irrelevant}, not a {label}. "
                        f"Please attach a valid {label}.",
                        detected=irrelevant, filename=filename, reasons=reasons)

    if accepted and detected and detected not in accepted:
        return _verdict("WRONG_TYPE", "error",
                        f"This looks like a {detected.replace('_',' ')}, but a {label} is "
                        f"needed here.",
                        detected=detected, filename=filename, reasons=reasons)

    # Not recognising the document is never grounds for approving it.
    #
    # This check used to be gated on `accepted` being populated. Every document
    # a category profile adds — identity proof, insurance, trade licences —
    # declares no accepted list, so the gate never fired and the file fell
    # through to "Looks like a valid X". A cover letter dropped into the photo
    # ID slot came back with a green tick. The absence of an expected-types
    # list means we cannot check the type, which is the opposite of the type
    # being fine.
    if detected is None:
        return _verdict("UNCONFIRMED", "warn",
                        f"We couldn't confirm this is a {label}. Check you attached the "
                        f"right file — a reviewer will look at it either way.",
                        detected=None, filename=filename, reasons=reasons)

    # Optional name cross-check (skip bank docs — they name the account holder).
    is_bank = (detected or "").startswith(("bank", "cancelled", "voided"))
    if legal_name and not is_bank:
        doc_name = read.fields.get("name")
        if doc_name and name_verdict(name_score(legal_name, doc_name)) == "MISMATCH":
            return _verdict("NAME_MISMATCH", "warn",
                            f"The name on this document ('{doc_name}') doesn't match "
                            f"'{legal_name}'. Make sure it's the right company's document.",
                            detected=detected, filename=filename)

    low = read.confidence < MIN_READ_CONFIDENCE
    return _verdict("OK", "warn" if low else "ok",
                    (f"Looks like a valid {label}."
                     + (" (a little hard to read — a clearer copy would help)" if low else "")),
                    detected=detected, filename=filename)


def _read_tmp(doc, path: Path):
    from backend.app.checks import document_reader as dr
    return dr._read_data(path.read_bytes(), path.suffix.lower())


def _verdict(status: str, level: str, message: str, *, detected, filename,
             reasons: list | None = None) -> dict[str, Any]:
    return {"status": status, "level": level, "message": message,
            "detected_type": detected, "filename": filename,
            "reasons": reasons or []}
