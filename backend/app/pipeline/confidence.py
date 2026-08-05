"""AI confidence score, and the routing it drives.

The severity model answers "what is wrong". This answers "how sure are we" —
and the two together decide whether a human needs to look.

    high confidence + nothing wrong        -> Auto Approve
    high confidence + clear disqualifier   -> Auto Reject
    anything else                          -> Manual Review

Confidence is built from things we can actually measure, not a vibe:

  * how well the documents were READ (a crisp PDF text layer is worth more
    than a marginal OCR of a phone photo)
  * how confidently each document was CLASSIFIED as the thing it claims to be
  * how much of the form is CORROBORATED by those documents
  * whether anything is ambiguous (unreadable, unconfirmed, unevidenced)

Every component is reported, so the number is explainable — a reviewer can see
*why* confidence was 0.62 rather than being handed an opaque score.
"""

from __future__ import annotations

from typing import Any

from backend.app.models import CheckResult, Finding, Severity, Status

# Findings that mean "we could not be sure", as opposed to "we are sure this
# is wrong". These reduce confidence; the definite ones don't.
# NOTE: FIELD_UNEVIDENCED is deliberately NOT here. It already lowers the
# corroboration component, and counting it twice punished genuinely clean
# vendors (a US W-9 simply doesn't carry a state registration number) hard
# enough to bounce them to a human for no reason.
_AMBIGUITY_CODES = {
    "DOCUMENT_UNREADABLE", "DOCUMENT_LOW_CONFIDENCE", "DOCUMENT_TYPE_MISMATCH",
    "REGISTRY_NOT_FOUND", "DENIED_PARTY_NEAR_MATCH",
    "SEMANTIC_RULE_FLAGGED", "UNSUPPORTED_COUNTRY", "VENDOR_AMBIGUOUS",
}

# Findings that are decisive enough to auto-reject on, when confidence is high.
_DISQUALIFYING_CODES = {"DENIED_PARTY_MATCH"}


def compute(results: list[CheckResult], findings: list[Finding]) -> dict[str, Any]:
    """Return {score, components, reasons} for this submission."""
    components: dict[str, float] = {}
    reasons: list[str] = []

    # --- 1. document read quality -----------------------------------------
    docs = next((r.data.get("documents", []) for r in results
                 if r.check == "documents"), [])
    if docs:
        reads = [float(d.get("read_confidence") or 0) for d in docs]
        classifies = [float(d.get("classify_confidence") or 0) for d in docs]
        components["document_read"] = round(sum(reads) / len(reads), 3)
        components["document_classification"] = round(
            sum(classifies) / len(classifies), 3)
        weak = [d for d in docs if float(d.get("read_confidence") or 0) < 0.7]
        if weak:
            reasons.append(f"{len(weak)} document(s) read at low confidence.")
    else:
        # No documents at all: we cannot corroborate anything from evidence.
        components["document_read"] = 0.5
        components["document_classification"] = 0.5
        reasons.append("No documents were supplied to verify against.")

    # --- 2. how much of the form the documents actually back --------------
    matrix = next((r.data.get("matrix", []) for r in results
                   if r.check == "field_verification"), [])
    if matrix:
        corroborated = sum(1 for m in matrix if m["outcome"] == "CORROBORATED")
        components["form_corroboration"] = round(corroborated / len(matrix), 3)
        gaps = len(matrix) - corroborated
        if gaps:
            reasons.append(f"{gaps} of {len(matrix)} form fields not corroborated "
                           f"by a document.")
    else:
        components["form_corroboration"] = 0.5
        reasons.append("No document-backed fields to compare.")

    # --- 3. ambiguity penalty ---------------------------------------------
    ambiguous = [f for f in findings if f.code.value in _AMBIGUITY_CODES]
    penalty = min(0.4, 0.1 * len(ambiguous))
    components["certainty"] = round(1.0 - penalty, 3)
    if ambiguous:
        reasons.append(f"{len(ambiguous)} finding(s) the system could not "
                       f"resolve on its own.")

    # Weighted mean — corroboration matters most, then read quality.
    weights = {"form_corroboration": 0.4, "document_read": 0.25,
               "document_classification": 0.15, "certainty": 0.2}
    score = sum(components[k] * w for k, w in weights.items())
    score = round(max(0.0, min(1.0, score)), 3)

    if not reasons:
        reasons.append("Documents read cleanly and every checked field matched.")

    return {"score": score, "components": components,
            "weights": weights, "reasons": reasons}


def route(severity_status: Status, confidence: float, findings: list[Finding],
          threshold: float) -> tuple[Status, str]:
    """Combine the severity verdict with confidence into a final decision.

    Returns (final_status, explanation). The rule is deliberately conservative:
    confidence can only ever move a case TOWARDS a human, never away from one.
    """
    disqualifying = [f for f in findings if f.code.value in _DISQUALIFYING_CODES]

    # A definite disqualifier rejects regardless of the score — we do not need
    # to be confident about the paperwork to know we cannot onboard a
    # sanctioned party.
    if disqualifying:
        return Status.REJECTED, (
            f"Auto-rejected: {disqualifying[0].code.value.replace('_', ' ').lower()}. "
            f"This is decisive on its own.")

    # Anything a human must judge stays with the human, whatever the score.
    if severity_status is Status.PENDING_REVIEW:
        return Status.PENDING_REVIEW, (
            f"Sent to manual review: findings require a human judgement "
            f"(confidence {confidence:.0%}).")

    # Missing/malformed input goes back to the vendor — also unaffected.
    if severity_status is Status.PENDING_INFO:
        return Status.PENDING_INFO, (
            f"Returned to the vendor: information is missing or malformed "
            f"(confidence {confidence:.0%}).")

    # Nothing is wrong. Now the score decides whether we trust that enough to
    # approve without a person looking.
    if confidence >= threshold:
        return Status.APPROVED, (
            f"Auto-approved: all checks passed and confidence is "
            f"{confidence:.0%} (threshold {threshold:.0%}).")

    return Status.PENDING_REVIEW, (
        f"Sent to manual review: no problems were found, but confidence is only "
        f"{confidence:.0%}, below the {threshold:.0%} auto-approval threshold — "
        f"the evidence was too weak to decide alone.")


def recommendation(status: Status) -> str:
    return {
        Status.APPROVED: "Approve",
        Status.REJECTED: "Reject",
        Status.PENDING_INFO: "Request more information",
        Status.PENDING_REVIEW: "Manual review",
    }[status]
