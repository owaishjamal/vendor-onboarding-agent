"""Run every check, aggregate the findings, decide once.

CONTRAST WITH AN INVOICE PIPELINE
    An invoice pipeline short-circuits: the moment a check is decisive it
    stops, because further work cannot change the answer and costs money.

    This one deliberately does not. Every check runs on every submission, even
    when an earlier one has already produced a REJECT. Two reasons:

      1. The vendor must receive ONE message listing everything wrong, not a
         drip of one item per round trip. That is only possible if every check
         has run.

      2. A reviewer looking at a rejected case still needs the full picture.
         "Rejected on a sanctions match" is thinner than "rejected on a
         sanctions match, and separately the bank account belongs to another
         vendor and the tax ID is from a different country" — the second tells
         you something about the submission as a whole.

    The cost is that every submission does the full amount of work. Onboarding
    volume is dozens per quarter, not thousands per day, so that is the right
    trade. It would be the wrong trade for invoices.

THE DECISION
    status = SEVERITY_TO_STATUS[max(severity of all findings)]

    That is the entire rule. There is no weighting, no score, no tuned
    threshold to argue about. Each check decides how serious its own finding
    is, at the point where it has the context to judge, and the status falls
    out of the most serious thing present.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Iterator, Optional

from backend.app import config
from backend.app.checks import (
    completeness, consistency, custom_rules, documents, duplicates,
    field_verification, formats, registry, screening,
)
from backend.app.llm.client import get_llm
from backend.app.pipeline import confidence
from backend.app.models import (
    CaseRecord, CheckResult, Finding, FindingCode, Severity, Status,
    SEVERITY_TO_STATUS, VendorSubmission,
)

# Order matters only for presentation - each check is independent and none
# consumes another's output. They are sequenced cheapest-to-most-contextual so
# the live view reads naturally: is it all here, is it well formed, does it
# agree with itself, does the paperwork back it up, who are these people,
# have we seen them before.
# The verification pipeline. Every check is independent — none consumes
# another's output — and every one runs on every submission, so a vendor is
# told everything that's wrong in a single response rather than one item per
# round trip.
CHECKS = [
    ("completeness", "Completeness", completeness.run),
    ("formats", "Format validation", formats.run),
    ("consistency", "Cross-field consistency", consistency.run),
    ("documents", "Document verification", documents.run),
    ("field_verification", "Form vs document comparison", field_verification.run),
    ("custom_rules", "Client rules", custom_rules.run),
    ("registry", "Registry verification", registry.run),
    ("screening", "Denied-party screening", screening.run),
    ("duplicates", "Duplicate & shared-banking check", duplicates.run),
]

CHECK_PLAN = [{"check": c, "label": l} for c, l, _ in CHECKS]


def _profile_for(sub):
    """The profile governing this submission (None-safe)."""
    try:
        from backend.app.profiles.store import get_profile
        return get_profile(getattr(sub, "profile_id", None), getattr(sub, "country", "") or "")
    except Exception:
        return None


def plan_for(sub) -> list[dict[str, str]]:
    """The check plan the UI renders while a submission is running."""
    return CHECK_PLAN


def _pace() -> None:
    if config.CHECK_DELAY_MS:
        time.sleep(config.CHECK_DELAY_MS / 1000.0)


def decide(findings: list[Finding]) -> Status:
    """Status is a pure function of the highest severity present."""
    if not findings:
        return Status.APPROVED
    return SEVERITY_TO_STATUS[max(f.severity for f in findings)]


def _finding_dict(f: Finding) -> dict[str, Any]:
    return {
        "code": f.code.value,
        "severity": int(f.severity),
        "severity_name": f.severity.name,
        "check": f.check,
        "field": f.field,
        "message": f.message,
        "vendor_message": f.vendor_message,
        "evidence": f.evidence,
    }


def build_vendor_items(findings: list[Finding], status: Status) -> list[str]:
    """The only findings a vendor is ever told about — and only sometimes.

    TWO GATES, BOTH LOAD-BEARING.

    Gate 1 — status. A vendor email is generated ONLY for PENDING_INFO.

        This is not a nicety. A rejected vendor is rejected because of a
        denied-party match; emailing them a friendly request for a bank letter
        tells a sanctioned party exactly which control caught them, and starts
        a correspondence with someone the business is legally barred from
        transacting with.

        PENDING_REVIEW is suppressed for a softer but similar reason: while a
        human is deciding whether an account belongs to who it claims to,
        contacting the submitter can tip off a fraudster and taints the
        review. Resolve internally first, then ask.

        So a rejected case can carry a perfectly valid "your bank letter is
        missing" finding and still produce no email at all. That is correct.

    Gate 2 — severity. Only NEEDS_INFO findings ever become vendor text.
        Filtering on severity rather than on "does this finding happen to have
        a vendor_message" means a NEEDS_REVIEW finding cannot leak into
        vendor-facing output even if someone later attaches a vendor_message
        to one by mistake. The rule lives here, in one place, rather than
        depending on every check author remembering it.
    """
    if status is not Status.PENDING_INFO:
        return []

    seen: set[str] = set()
    items: list[str] = []
    for f in findings:
        if f.severity is not Severity.NEEDS_INFO:
            continue
        msg = (f.vendor_message or "").strip()
        if msg and msg not in seen:
            seen.add(msg)
            items.append(msg)
    return items


def assess(sub: VendorSubmission) -> tuple[Status, list[Finding], list[CheckResult], dict]:
    """Run every check and decide, WITHOUT persistence or LLM calls.

    This is the pure core of the pipeline. run_pipeline() wraps it with storage
    and the generated documents; the volume evaluator uses it directly so it can
    score hundreds of submissions in a couple of seconds. Keeping it separate
    also means the decision logic is testable in isolation from the plumbing.
    """
    all_findings: list[Finding] = []
    results: list[CheckResult] = []
    for name, label, fn in CHECKS:
        try:
            result = fn(sub)
        except Exception as exc:  # a broken check escalates, never approves
            result = CheckResult(
                check=name, label=label,
                summary=f"Check failed to run: {type(exc).__name__}: {exc}",
                findings=[Finding(
                    code=FindingCode.MISSING_REQUIRED_FIELD,
                    severity=Severity.NEEDS_REVIEW, check=name,
                    message=f"The '{label}' check could not be completed ({exc}).")],
            )
        results.append(result)
        all_findings.extend(result.findings)
    severity_status = decide(all_findings)
    conf = confidence.compute(results, all_findings)
    final, why = confidence.route(severity_status, conf["score"], all_findings,
                                  config.AUTO_DECIDE_CONFIDENCE)
    conf["decision_reason"] = why
    conf["severity_status"] = severity_status.value
    return final, all_findings, results, conf


def run_pipeline(sub: VendorSubmission,
                 case_id: Optional[str] = None) -> Iterator[dict[str, Any]]:
    """Execute all checks, yielding an event per completed check.

    Yields:
        {"type": "check", "result": CheckResult-as-dict}
        {"type": "done",  "case": {...}}
    """
    from backend.app.storage import cases as casestore

    cid = case_id or uuid.uuid4().hex[:12]
    casestore.create_case(cid, sub)

    all_findings: list[Finding] = []
    results: list[CheckResult] = []
    seq = 0

    try:
        for name, label, fn in CHECKS:
            _pace()
            try:
                result = fn(sub)
            except Exception as exc:
                # A check that crashes must not silently vanish. It becomes a
                # NEEDS_REVIEW finding, because "we could not run this control"
                # is never grounds for approval.
                result = CheckResult(
                    check=name, label=label,
                    summary=f"Check failed to run: {type(exc).__name__}: {exc}",
                    findings=[Finding(
                        code=FindingCode.MISSING_REQUIRED_FIELD,
                        severity=Severity.NEEDS_REVIEW, check=name,
                        message=(f"The '{label}' check could not be completed "
                                 f"({type(exc).__name__}: {exc}). This submission cannot "
                                 f"be approved without it."),
                    )],
                )

            results.append(result)
            all_findings.extend(result.findings)
            seq += 1
            casestore.append_check(cid, seq, result)
            yield {"type": "check", "result": result.model_dump(mode="json")}

        severity_status = decide(all_findings)
        conf = confidence.compute(results, all_findings)
        status, why = confidence.route(severity_status, conf["score"], all_findings,
                                       config.AUTO_DECIDE_CONFIDENCE)
        conf["decision_reason"] = why
        conf["severity_status"] = severity_status.value
        conf["recommendation"] = confidence.recommendation(status)

        if not all_findings:
            all_findings.append(Finding(
                code=FindingCode.ALL_CHECKS_PASSED, severity=Severity.INFO,
                check="decision", message="All checks passed with no findings.",
            ))

        payload = {
            "status": status.value,
            "legal_name": sub.legal_name,
            "contact_name": sub.contact_name,
            "country": sub.country,
            "findings": [_finding_dict(f) for f in all_findings],
            "vendor_items": build_vendor_items(all_findings, status),
            "confidence": conf["score"],
            "decision_reason": why,
            "suppressed_vendor_items": (
                # Recorded so a reviewer can see what WOULD have been asked for
                # once the internal question is resolved. Visible internally,
                # never sent.
                [f.vendor_message for f in all_findings
                 if f.severity is Severity.NEEDS_INFO and f.vendor_message]
                if status is not Status.PENDING_INFO else []
            ),
        }

        _pace()
        llm = get_llm()
        email, _ = llm.draft_vendor_email(payload)
        summary, _ = llm.reviewer_summary(payload)

        casestore.complete_case(cid, status=status, findings=all_findings,
                                reviewer_summary=summary, vendor_email=email or None,
                                confidence=conf)

        yield {"type": "done", "case": casestore.get_case(cid)}

    except Exception as exc:  # pragma: no cover - defensive
        casestore.fail_case(cid, f"{type(exc).__name__}: {exc}")
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
