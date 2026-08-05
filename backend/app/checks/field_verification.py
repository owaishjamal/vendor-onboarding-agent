"""Evidence-first field verification.

Form fields are CLAIMS; documents are EVIDENCE. This check enforces the order
the whole design now rests on:

  Stage 1 — validate documents (DVA). Only passing documents are admissible.
  Stage 2 — extract an evidence store from admissible documents.
  Stage 3 — verify every profile field that declares an evidence mapping.

Each mapped field gets one of four outcomes, rendered as a verification
matrix in the case detail:

  CORROBORATED  claim matches admissible evidence            -> INFO
  CONTRADICTED  admissible evidence says something else      -> NEEDS_REVIEW
  UNEVIDENCED   no admissible evidence backs the claim       -> ADVISORY*
  UNVERIFIABLE  the field declares no evidence source        -> (not in matrix)

* ADVISORY, not NEEDS_INFO, because the missing/failed DOCUMENT itself is
  already a NEEDS_INFO finding from completeness / the DVA — flagging the
  field again would double-bill the vendor for one problem. The matrix still
  shows it, and the reviewer summary can say "2 of 5 fields unevidenced".

Document reads are cached by file hash, so re-reading here after the
documents check costs microseconds, keeping checks independent (no shared
mutable state between them — same architecture rule as always).
"""

from __future__ import annotations

from typing import Any, Optional

from backend.app.checks.base import Timer, finding, name_score, name_verdict
from backend.app.checks.documents import _accepted_kinds_for
from backend.app.dva.agent import DocStatus, verify
from backend.app.models import (
    CheckResult, Finding, FindingCode, Severity, VendorSubmission,
)
from backend.app.profiles.store import get_profile
from backend.app.rules import load_common_rules

CHECK = "field_verification"


def _get_claim(sub: VendorSubmission, key: str) -> Optional[str]:
    """Resolve a field key: core attr, dotted bank path, or custom field."""
    if key.startswith("bank."):
        v = getattr(sub.bank, key.split(".", 1)[1], None)
    elif key in sub.custom_fields:
        v = sub.custom_fields.get(key)
    else:
        v = getattr(sub, key, None)
    return str(v).strip() if v not in (None, "") else None


def _norm_id(v: str) -> str:
    import re
    return re.sub(r"[\s\-]", "", v).upper()


def build_evidence(sub: VendorSubmission, profile) -> tuple[dict, list[dict]]:
    """Stages 1+2: DVA-validate every document, extract admissible evidence.

    Returns (evidence_store, doc_statuses). Evidence keys are
    "<doc_key>.<field>" e.g. "bank_proof.name", "tax_form.number".
    """
    doc_rules = load_common_rules().get("document_rules", {})
    freshness = set(doc_rules.get("freshness_required", []))
    max_age = doc_rules.get("max_age_months", 12)

    evidence: dict[str, dict[str, Any]] = {}
    statuses: list[dict] = []

    for doc in sub.documents:
        verdict = verify(doc, sub, _accepted_kinds_for(sub, doc.doc_type),
                         freshness_required=freshness, max_age_months=max_age)
        admissible = verdict.status == DocStatus.VERIFIED or (
            # A NEEDS_REVIEW verdict means the doc IS the right type but
            # contradicts the form — its evidence is exactly what we need to
            # surface, so it stays admissible. Only wrong/irrelevant/unreadable
            # documents (NEEDS_INFO) contribute nothing.
            verdict.status == DocStatus.NEEDS_REVIEW
        )
        statuses.append({"doc_key": doc.doc_type, "filename": doc.filename,
                         "status": verdict.status, "admissible": admissible,
                         "detected_type": verdict.detected_type})
        if not admissible:
            continue

        from backend.app.checks.document_reader import read_document
        read = read_document(doc)   # cached — effectively free
        if read.fields.get("name"):
            evidence[f"{doc.doc_type}.name"] = {
                "value": read.fields["name"], "source": doc.filename,
                "confidence": read.confidence}
        if read.fields.get("number"):
            evidence[f"{doc.doc_type}.number"] = {
                "value": read.fields["number"], "source": doc.filename,
                "confidence": read.confidence}

    return evidence, statuses


def run(sub: VendorSubmission) -> CheckResult:
    findings: list[Finding] = []
    matrix: list[dict[str, Any]] = []

    with Timer() as t:
        profile = get_profile(sub.profile_id, sub.country)
        evidence, doc_statuses = build_evidence(sub, profile)

        for spec in profile.fields:
            sources = spec.validation_source
            if not sources:
                continue                      # UNVERIFIABLE — no external source
            claim = _get_claim(sub, spec.key)
            if claim is None:
                continue                      # missing field: completeness owns it

            found = [(k, evidence[k]) for k in sources if k in evidence]
            row: dict[str, Any] = {"field": spec.key, "claim": claim,
                                   "evidence_keys": sources}

            if not found:
                row["outcome"] = "UNEVIDENCED"
                row["detail"] = "No admissible document backs this value."
                findings.append(finding(
                    FindingCode.FIELD_UNEVIDENCED, Severity.ADVISORY, CHECK,
                    message=(f"'{spec.label}' ({claim}) is not corroborated by any "
                             f"admissible document (expected from: "
                             f"{', '.join(sources)})."),
                    field=spec.key, claim=claim, expected_sources=sources,
                ))
            else:
                # Compare against the best evidence entry.
                is_idlike = spec.type in ("id", "iban", "aba", "number")
                best_key, best = max(found, key=lambda kv: kv[1]["confidence"])
                ev_val = str(best["value"])
                if is_idlike:
                    match = _norm_id(claim) == _norm_id(ev_val)
                    score = 100.0 if match else 0.0
                else:
                    score = name_score(claim, ev_val)
                    match = name_verdict(score) != "MISMATCH"

                row["evidence_value"] = ev_val
                row["source"] = best["source"]
                row["score"] = round(score, 1)

                if match:
                    row["outcome"] = "CORROBORATED"
                    findings.append(finding(
                        FindingCode.FIELD_CORROBORATED, Severity.INFO, CHECK,
                        message=(f"'{spec.label}' corroborated by {best['source']} "
                                 f"({best_key})."),
                        field=spec.key, claim=claim, evidence=ev_val,
                        source=best["source"],
                    ))
                else:
                    row["outcome"] = "CONTRADICTED"
                    # The serious case — but the DVA already raises the
                    # name-level contradictions (DOCUMENT_NAME_MISMATCH), so to
                    # avoid double findings we only escalate here for id-like
                    # exact fields the DVA treats as advisory.
                    sev = Severity.NEEDS_REVIEW if is_idlike else Severity.ADVISORY
                    findings.append(finding(
                        FindingCode.FIELD_CONTRADICTED, sev, CHECK,
                        message=(f"'{spec.label}' is stated as '{claim}' but the "
                                 f"admissible document ({best['source']}) shows "
                                 f"'{ev_val}'. The evidence contradicts the claim."),
                        field=spec.key, claim=claim, evidence=ev_val,
                        source=best["source"],
                    ))
            matrix.append(row)

    corroborated = sum(1 for r in matrix if r["outcome"] == "CORROBORATED")
    contradicted = sum(1 for r in matrix if r["outcome"] == "CONTRADICTED")
    unevidenced = sum(1 for r in matrix if r["outcome"] == "UNEVIDENCED")
    if not matrix:
        summary = "No document-backed fields to verify for this profile."
    elif contradicted:
        summary = (f"{corroborated}/{len(matrix)} claims corroborated; "
                   f"{contradicted} CONTRADICTED by documents.")
    elif unevidenced:
        summary = (f"{corroborated}/{len(matrix)} claims corroborated; "
                   f"{unevidenced} lack admissible evidence.")
    else:
        summary = f"All {len(matrix)} document-backed claims corroborated by evidence."

    return CheckResult(check=CHECK, label="Field-vs-evidence verification",
                       findings=findings, summary=summary, duration_ms=t.ms,
                       data={"matrix": matrix, "documents": doc_statuses,
                             "profile_id": profile.profile_id})
