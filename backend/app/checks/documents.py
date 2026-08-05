"""Document verification check — a thin orchestrator over the DVA.

Each attached document is verified by the Document Verification Agent
(`dva/agent.py`): read it, classify it FROM ITS CONTENT (not its heading),
cross-reference the name/number against the form, and check it is readable, in
date and internally coherent. The agent returns a per-document verdict; this
check gathers them into findings and an honest summary.

What this makes real, straight out of the problem statement:

  * "attach the wrong documents" — content classification catches a CV, a
    delivery note, or a certificate submitted where a bank letter belongs,
    even for layouts we have never seen.
  * subtle inconsistencies — the name on the actual document is compared to the
    form, so a certificate describing a different entity is surfaced.
  * we don't trust what we can't read — a low-confidence read is sent back for
    a clearer copy, never accepted on a guess.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from backend.app.checks.base import Timer
from backend.app.dva.agent import DocStatus, verify
from backend.app.models import CheckResult, Finding, VendorSubmission
from backend.app.rules import load_common_rules, required_documents

CHECK = "documents"


def _accepted_kinds_for(sub: VendorSubmission, doc_type: str) -> set[str]:
    """Which document kinds satisfy this slot — from the PROFILE first.

    A profile can declare slots the country packs never heard of (a food
    licence, an invoice PDF), so the profile is authoritative; the country
    pack is the fallback for the default onboarding shape.
    """
    from backend.app.profiles.store import get_profile
    try:
        for spec in get_profile(sub.profile_id, sub.country).documents:
            if spec.key == doc_type:
                # A custom slot with only an `expects` description has no
                # enumerated kinds — the DVA verifies it against the prose.
                return set(spec.accepted or [])
    except Exception:
        pass
    for spec in required_documents(sub.country):
        if spec["doc_type"] == doc_type:
            return set(spec.get("accepted", []))
    return set()


def run(sub: VendorSubmission, today: date | None = None) -> CheckResult:
    findings: list[Finding] = []
    today = today or date.today()
    doc_rules = load_common_rules().get("document_rules", {})
    max_age = doc_rules.get("max_age_months", 12)
    freshness_required = set(doc_rules.get("freshness_required", []))
    per_doc: list[dict[str, Any]] = []

    with Timer() as t:
        for doc in sub.documents:
            verdict = verify(
                doc, sub, _accepted_kinds_for(sub, doc.doc_type),
                freshness_required=freshness_required, max_age_months=max_age,
                today=today,
            )
            findings.extend(verdict.findings)
            per_doc.append(verdict.as_dict())

    # "Corroborate" is only honest for documents the agent actually VERIFIED
    # (right type + consistent). Everything else was flagged, not passed.
    verified = sum(1 for d in per_doc if d["status"] == DocStatus.VERIFIED)
    if not sub.documents:
        summary = "No documents attached to verify."
    elif findings:
        summary = f"{len(sub.documents)} document(s) checked; {len(findings)} issue(s) found."
    else:
        summary = f"{len(sub.documents)} document(s) read, classified and corroborate the form."

    return CheckResult(check=CHECK, label="Document verification", findings=findings,
                       summary=summary, duration_ms=t.ms,
                       data={"documents": per_doc, "verified": verified})
