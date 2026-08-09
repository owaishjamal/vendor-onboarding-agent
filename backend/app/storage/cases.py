"""Case persistence and reviewer-queue queries."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, insert, update, desc, func

from backend.app.models import (
    CheckResult, Finding, Severity, Status, VendorSubmission,
)
from backend.app.storage.db import (
    get_conn, onboarding_case, case_check, case_finding, case_action
)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def entity_key(sub: VendorSubmission) -> str:
    """A stable identity for a vendor across resubmissions.

    Registration number is the strongest signal, then tax ID, then name+country
    as a fallback. Normalised so formatting differences don't split one vendor
    into two identities.
    """
    import re
    reg = re.sub(r"[\s\-]", "", (sub.registration_number or "")).upper()
    if reg:
        return f"{sub.country.upper()}:REG:{reg}"
    tax = re.sub(r"[\s\-]", "", (sub.tax_id or "")).upper()
    if tax:
        return f"{sub.country.upper()}:TAX:{tax}"
    name = re.sub(r"[^a-z0-9]", "", (sub.legal_name or "").lower())
    return f"{sub.country.upper()}:NAME:{name}"


def create_case(case_id: str, sub: VendorSubmission) -> None:
    import secrets
    key = entity_key(sub)
    vendor_token = secrets.token_urlsafe(24)
    with get_conn() as c:
        stmt = (
            select(onboarding_case.c.case_id, onboarding_case.c.revision)
            .where(
                (onboarding_case.c.entity_key == key) &
                (onboarding_case.c.superseded_by.is_(None)) &
                (onboarding_case.c.case_id != case_id)
            )
            .order_by(desc(onboarding_case.c.created_at))
            .limit(1)
        )
        prior = c.execute(stmt).fetchone()
        
        # SQLAlchemy returns a Row which supports getattr
        revision = (prior.revision + 1) if prior else 1
        supersedes = prior.case_id if prior else None

        c.execute(
            insert(onboarding_case).values(
                case_id=case_id,
                legal_name=sub.legal_name,
                trading_name=sub.trading_name,
                country=sub.country,
                contact_email=sub.contact_email,
                status="RUNNING",
                submission=sub.model_dump_json(),
                created_at=_now(),
                entity_key=key,
                revision=revision,
                supersedes=supersedes,
                vendor_token=vendor_token,
                profile_id=sub.profile_id or "default"
            )
        )


def append_check(case_id: str, seq: int, r: CheckResult) -> None:
    with get_conn() as c:
        c.execute(
            insert(case_check).values(
                case_id=case_id,
                seq=seq,
                check_name=r.check,
                label=r.label,
                summary=r.summary,
                data=json.dumps(r.data, default=str),
                duration_ms=r.duration_ms,
                created_at=_now()
            )
        )


# "Blocking" means at least NEEDS_INFO: something the vendor or a reviewer has
# to act on. Referenced by value rather than a literal so that inserting a new
# severity (CONDITION) cannot silently redefine what counts as blocking — which
# is exactly what a bare `>= 2` did.
BLOCKING_SEVERITY = int(Severity.NEEDS_INFO)


def _blocking_codes(rows) -> set[str]:
    return {r.code for r in rows
            if getattr(r, "severity", 0) >= BLOCKING_SEVERITY}


def complete_case(case_id: str, status: Status, findings: list[Finding],
                  reviewer_summary: str, vendor_email: Optional[str],
                  confidence: Optional[dict] = None) -> None:
    with get_conn() as c:
        if findings:
            c.execute(
                insert(case_finding),
                [
                    {
                        "case_id": case_id,
                        "code": f.code.value,
                        "severity": int(f.severity),
                        "severity_name": f.severity.name,
                        "check_name": f.check,
                        "field": f.field,
                        "message": f.message,
                        "vendor_message": f.vendor_message,
                        "evidence": json.dumps(f.evidence, default=str),
                        "created_at": _now()
                    }
                    for f in findings
                ]
            )

        # --- resubmission diff, if this replaces a prior attempt
        change_summary = None
        row = c.execute(
            select(onboarding_case.c.supersedes)
            .where(onboarding_case.c.case_id == case_id)
        ).fetchone()
        
        prior_id = row.supersedes if row else None
        if prior_id:
            prior = c.execute(
                select(case_finding.c.code, case_finding.c.severity)
                .where(case_finding.c.case_id == prior_id)
            ).fetchall()
            prior_codes = _blocking_codes(prior)
            now_codes = {f.code.value for f in findings
                         if int(f.severity) >= BLOCKING_SEVERITY}
            resolved = sorted(prior_codes - now_codes)
            new = sorted(now_codes - prior_codes)
            remaining = sorted(prior_codes & now_codes)
            change_summary = json.dumps({
                "prior_case": prior_id,
                "resolved": resolved, "new": new, "remaining": remaining,
            })
            # Mark the prior case as superseded by this one.
            c.execute(
                update(onboarding_case)
                .where(onboarding_case.c.case_id == prior_id)
                .values(superseded_by=case_id)
            )

        c.execute(
            update(onboarding_case)
            .where(onboarding_case.c.case_id == case_id)
            .values(
                status=status.value,
                reviewer_summary=reviewer_summary,
                vendor_email=vendor_email,
                completed_at=_now(),
                change_summary=change_summary,
                confidence=json.dumps(confidence or {}, default=str)
            )
        )


# ---------------------------------------------------------------------------
# Reviewer actions
# ---------------------------------------------------------------------------

ACTION_RESULT = {
    "approve":      ("APPROVED_BY_REVIEWER", "APPROVED"),
    "reject":       ("REJECTED_BY_REVIEWER", "REJECTED"),
    "request_info": ("PENDING_INFO", "INFO_REQUESTED"),
    "resolve":      (None, "RESOLVED"),
    "reopen":       (None, "REOPENED"),
}


def record_action(case_id: str, action: str, reviewer: Optional[str],
                  note: Optional[str]) -> dict[str, Any]:
    if action not in ACTION_RESULT:
        raise ValueError(f"unknown action: {action}")
    new_status, resolution = ACTION_RESULT[action]

    with get_conn() as c:
        row = c.execute(
            select(onboarding_case.c.status)
            .where(onboarding_case.c.case_id == case_id)
        ).fetchone()
        
        if not row:
            raise KeyError(case_id)
        prev = row.status
        applied = new_status or prev

        c.execute(
            insert(case_action).values(
                case_id=case_id,
                action=action,
                reviewer=reviewer,
                note=note,
                prev_status=prev,
                new_status=applied,
                created_at=_now()
            )
        )
        c.execute(
            update(onboarding_case)
            .where(onboarding_case.c.case_id == case_id)
            .values(status=applied, resolution=resolution)
        )
        
    return {"case_id": case_id, "action": action, "prev_status": prev,
            "new_status": applied, "resolution": resolution}


def list_actions(case_id: str) -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            select(case_action)
            .where(case_action.c.case_id == case_id)
            .order_by(case_action.c.id)
        ).fetchall()
        
    return [{"action": getattr(r, "action", None), 
             "reviewer": getattr(r, "reviewer", None), 
             "note": getattr(r, "note", None),
             "prev_status": getattr(r, "prev_status", None), 
             "new_status": getattr(r, "new_status", None),
             "created_at": getattr(r, "created_at", None)} for r in rows]



def fail_case(case_id: str, message: str) -> None:
    with get_conn() as c:
        c.execute(
            update(onboarding_case)
            .where(onboarding_case.c.case_id == case_id)
            .values(status='ERROR', reviewer_summary=message, completed_at=_now())
        )


def _col(r, name, default=None):
    """Tolerant column access."""
    val = getattr(r, name, default)
    return val if val is not None else default


def _row(r) -> dict[str, Any]:
    return {
        "case_id": r.case_id, "legal_name": r.legal_name,
        "trading_name": r.trading_name, "country": r.country,
        "contact_email": r.contact_email, "status": r.status,
        "reviewer_summary": r.reviewer_summary, "vendor_email": r.vendor_email,
        "created_at": r.created_at, "completed_at": r.completed_at,
        "revision": _col(r, "revision", 1),
        "supersedes": _col(r, "supersedes"),
        "superseded_by": _col(r, "superseded_by"),
        "resolution": _col(r, "resolution"),
        "vendor_token": _col(r, "vendor_token"),
        "profile_id": _col(r, "profile_id", "default"),
        "confidence": json.loads(_col(r, "confidence") or "{}"),
    }


# ---------------------------------------------------------------------------
# Vendor-safe view (the vendor portal's ONLY data source)
# ---------------------------------------------------------------------------

_VENDOR_STATUS = {
    "APPROVED": ("Approved", "Your onboarding is complete. No action needed."),
    "APPROVED_BY_REVIEWER": ("Approved", "Your onboarding is complete. No action needed."),
    "PENDING_INFO": ("Action needed", "We need a few things from you — see below."),
    "PENDING_REVIEW": ("In review", "Your submission is being reviewed. No action needed right now."),
    "REJECTED": ("Not approved", "We are unable to proceed with this onboarding."),
    "REJECTED_BY_REVIEWER": ("Not approved", "We are unable to proceed with this onboarding."),
    "RUNNING": ("Processing", "Your submission is being processed."),
}


def vendor_view(token: str) -> Optional[dict[str, Any]]:
    with get_conn() as c:
        r = c.execute(
            select(onboarding_case).where(onboarding_case.c.vendor_token == token)
        ).fetchone()
        if not r:
            return None
            
        status = r.status
        label, message = _VENDOR_STATUS.get(status, ("Processing", ""))

        items: list[str] = []
        if status == "PENDING_INFO":
            rows = c.execute(
                select(case_finding.c.vendor_message).distinct()
                .where(
                    (case_finding.c.case_id == r.case_id) &
                    (case_finding.c.severity == int(Severity.NEEDS_INFO)) &
                    (case_finding.c.vendor_message.is_not(None))
                )
            ).fetchall()
            items = [x.vendor_message for x in rows]

        return {
            "reference": r.case_id[:8].upper(),
            "legal_name": r.legal_name,
            "submitted_at": r.created_at,
            "status_label": label,
            "status_message": message,
            "action_needed": status == "PENDING_INFO",
            "items": items,
            "revision": _col(r, "revision", 1),
            "superseded_by": _col(r, "superseded_by"),
            "profile_id": _col(r, "profile_id", "default"),
            "country": r.country,
            "submission": json.loads(r.submission or "{}"),
        }


def token_case(token: str) -> Optional[dict[str, Any]]:
    with get_conn() as c:
        r = c.execute(
            select(onboarding_case.c.case_id)
            .where(onboarding_case.c.vendor_token == token)
        ).fetchone()
    return {"case_id": r.case_id} if r else None


def get_case(case_id: str, full: bool = True) -> Optional[dict[str, Any]]:
    with get_conn() as c:
        r = c.execute(
            select(onboarding_case).where(onboarding_case.c.case_id == case_id)
        ).fetchone()
        if not r:
            return None
        out = _row(r)
        out["submission"] = json.loads(r.submission or "{}")
        cs = _col(r, "change_summary")
        out["change_summary"] = json.loads(cs) if cs else None
        out["actions"] = list_actions(case_id)
        if full:
            checks = c.execute(
                select(case_check)
                .where(case_check.c.case_id == case_id)
                .order_by(case_check.c.seq)
            ).fetchall()
            
            from backend.app.pipeline.runner import CHECK_KIND
            out["checks"] = [{
                "seq": getattr(s, "seq"), "check": getattr(s, "check_name"), "label": getattr(s, "label"),
                "summary": getattr(s, "summary"), "data": json.loads(getattr(s, "data")),
                "duration_ms": getattr(s, "duration_ms"),
                # How the check decided — a rule or a model. Resolved on read
                # from the pipeline definition rather than stored, so the
                # answer stays correct if a check is reclassified later.
                "kind": CHECK_KIND.get(getattr(s, "check_name"), "deterministic"),
            } for s in checks]

            findings = c.execute(
                select(case_finding)
                .where(case_finding.c.case_id == case_id)
                .order_by(desc(case_finding.c.severity), case_finding.c.id)
            ).fetchall()
            
            out["findings"] = [{
                "code": getattr(f, "code"), "severity": getattr(f, "severity"),
                "severity_name": getattr(f, "severity_name"), "check": getattr(f, "check_name"),
                "field": getattr(f, "field"), "message": getattr(f, "message"),
                "vendor_message": getattr(f, "vendor_message"),
                "evidence": json.loads(getattr(f, "evidence") or "{}"),
            } for f in findings]
    return out


def list_cases(limit: int = 200) -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            select(onboarding_case)
            .order_by(desc(onboarding_case.c.created_at))
            .limit(limit)
        ).fetchall()
        
        out = []
        for r in rows:
            d = _row(r)
            
            counts = c.execute(
                select(case_finding.c.severity_name, func.count().label("n"))
                .where(case_finding.c.case_id == r.case_id)
                .group_by(case_finding.c.severity_name)
            ).fetchall()
            
            d["finding_counts"] = {x.severity_name: x.n for x in counts}
            
            top = c.execute(
                select(case_finding.c.code, case_finding.c.message)
                .where(case_finding.c.case_id == r.case_id)
                .order_by(desc(case_finding.c.severity), case_finding.c.id)
                .limit(1)
            ).fetchone()
            
            d["top_finding"] = {"code": top.code, "message": top.message} if top else None
            out.append(d)
    return out


def stats() -> dict[str, Any]:
    with get_conn() as c:
        status_counts = c.execute(
            select(onboarding_case.c.status, func.count().label("n"))
            .group_by(onboarding_case.c.status)
        ).fetchall()
        by = {r.status: r.n for r in status_counts}
        total = sum(by.values())
        
        case_sums = (
            select(func.sum(case_check.c.duration_ms).label("t"))
            .group_by(case_check.c.case_id)
            .subquery()
        )
        avg_res = c.execute(
            select(func.avg(case_sums.c.t).label("a"))
        ).fetchone()
        
        avg = avg_res.a if avg_res and avg_res.a else 0

        top_codes_rows = c.execute(
            select(case_finding.c.code, func.count().label("n"))
            .where(case_finding.c.severity >= BLOCKING_SEVERITY)
            .group_by(case_finding.c.code)
            .order_by(desc("n"))
            .limit(5)
        ).fetchall()
        top_codes = [{"code": r.code, "n": r.n} for r in top_codes_rows]

    decided = sum(v for k, v in by.items() if k != "RUNNING")
    auto = by.get("APPROVED", 0)
    return {
        "total_cases": total,
        "by_status": by,
        "auto_approved": auto,
        "touch_rate": round(100 * (decided - auto) / decided, 1) if decided else 0.0,
        "avg_duration_ms": int(avg or 0),
        "top_finding_codes": top_codes,
    }
