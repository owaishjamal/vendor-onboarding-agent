"""Case persistence and reviewer-queue queries."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from backend.app.models import (
    CheckResult, Finding, Status, VendorSubmission,
)
from backend.app.storage.db import get_conn


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


def create_case(case_id: str, sub: VendorSubmission, tenant: str = "demo") -> None:
    key = entity_key(sub)
    with get_conn() as c:
        # Prior attempts are scoped to the same tenant — one customer's
        # resubmission must never link to another's case.
        prior = c.execute(
            """SELECT case_id, revision FROM onboarding_case
                WHERE entity_key = ? AND org_id = ? AND superseded_by IS NULL
                  AND case_id != ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (key, tenant, case_id),
        ).fetchone()
        revision = (prior["revision"] + 1) if prior else 1
        supersedes = prior["case_id"] if prior else None

        c.execute(
            """INSERT INTO onboarding_case
               (case_id, legal_name, trading_name, country, contact_email,
                status, submission, created_at, entity_key, revision, supersedes, org_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (case_id, sub.legal_name, sub.trading_name, sub.country,
             sub.contact_email, "RUNNING", sub.model_dump_json(), _now(),
             key, revision, supersedes, tenant),
        )


def append_check(case_id: str, seq: int, r: CheckResult) -> None:
    with get_conn() as c:
        c.execute(
            """INSERT INTO case_check
               (case_id, seq, check_name, label, summary, data, duration_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (case_id, seq, r.check, r.label, r.summary,
             json.dumps(r.data, default=str), r.duration_ms, _now()),
        )


def _blocking_codes(rows) -> set[str]:
    return {r["code"] for r in rows if r["severity"] >= 2}


def complete_case(case_id: str, status: Status, findings: list[Finding],
                  reviewer_summary: str, vendor_email: Optional[str]) -> None:
    with get_conn() as c:
        for f in findings:
            c.execute(
                """INSERT INTO case_finding
                   (case_id, code, severity, severity_name, check_name, field,
                    message, vendor_message, evidence, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (case_id, f.code.value, int(f.severity), f.severity.name, f.check,
                 f.field, f.message, f.vendor_message,
                 json.dumps(f.evidence, default=str), _now()),
            )

        # --- resubmission diff, if this replaces a prior attempt
        change_summary = None
        row = c.execute("SELECT supersedes FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()
        prior_id = row["supersedes"] if row else None
        if prior_id:
            prior = c.execute(
                "SELECT code, severity FROM case_finding WHERE case_id = ?", (prior_id,)
            ).fetchall()
            prior_codes = _blocking_codes(prior)
            now_codes = {f.code.value for f in findings if int(f.severity) >= 2}
            resolved = sorted(prior_codes - now_codes)
            new = sorted(now_codes - prior_codes)
            remaining = sorted(prior_codes & now_codes)
            change_summary = json.dumps({
                "prior_case": prior_id,
                "resolved": resolved, "new": new, "remaining": remaining,
            })
            # Mark the prior case as superseded by this one.
            c.execute(
                "UPDATE onboarding_case SET superseded_by = ? WHERE case_id = ?",
                (case_id, prior_id),
            )

        c.execute(
            """UPDATE onboarding_case
                  SET status = ?, reviewer_summary = ?, vendor_email = ?,
                      completed_at = ?, change_summary = ?
                WHERE case_id = ?""",
            (status.value, reviewer_summary, vendor_email, _now(),
             change_summary, case_id),
        )


# ---------------------------------------------------------------------------
# Reviewer actions
# ---------------------------------------------------------------------------

# What each action does to the case status. The automated status is never
# overwritten silently — the reviewer's decision is recorded as a distinct
# resolution, and the status moves to an explicit human-decided value.
ACTION_RESULT = {
    "approve":      ("APPROVED_BY_REVIEWER", "APPROVED"),
    "reject":       ("REJECTED_BY_REVIEWER", "REJECTED"),
    "request_info": ("PENDING_INFO", "INFO_REQUESTED"),
    "resolve":      (None, "RESOLVED"),     # keep status, just record closure
    "reopen":       (None, "REOPENED"),
}


def record_action(case_id: str, action: str, reviewer: Optional[str],
                  note: Optional[str]) -> dict[str, Any]:
    if action not in ACTION_RESULT:
        raise ValueError(f"unknown action: {action}")
    new_status, resolution = ACTION_RESULT[action]

    with get_conn() as c:
        row = c.execute("SELECT status FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone()
        if not row:
            raise KeyError(case_id)
        prev = row["status"]
        applied = new_status or prev

        c.execute(
            """INSERT INTO case_action
               (case_id, action, reviewer, note, prev_status, new_status, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (case_id, action, reviewer, note, prev, applied, _now()),
        )
        c.execute(
            "UPDATE onboarding_case SET status = ?, resolution = ? WHERE case_id = ?",
            (applied, resolution, case_id),
        )
    return {"case_id": case_id, "action": action, "prev_status": prev,
            "new_status": applied, "resolution": resolution}


def override_report() -> dict[str, Any]:
    """Where reviewers disagree with the automated decision.

    This is the feedback loop the audit log makes possible. An override is a
    reviewer landing somewhere the checks did not recommend:

      * approved a case the system had held or rejected (the checks were too
        aggressive, OR the reviewer had outside context), or
      * rejected a case the system had passed or only queried (the checks
        missed something).

    Tallying overrides BY CHECK tells you which control is miscalibrated: if the
    bank-name check is overridden-to-approve ten times a month, it is crying
    wolf and the threshold wants revisiting. A static system can't tell you
    that; this turns the resolutions into a live calibration signal.
    """
    APPROVE_RES = {"APPROVED"}
    REJECT_RES = {"REJECTED"}

    with get_conn() as c:
        # First action per case carries the automated status in prev_status.
        cases_with_actions = c.execute(
            """SELECT case_id, MIN(id) AS first_id FROM case_action GROUP BY case_id"""
        ).fetchall()

        overrides = []
        by_check: dict[str, int] = {}
        by_code: dict[str, int] = {}
        total_resolved = 0

        for row in cases_with_actions:
            cid = row["case_id"]
            actions = list_actions(cid)
            terminal = next((a for a in actions
                             if a["new_status"] in ("APPROVED_BY_REVIEWER", "REJECTED_BY_REVIEWER")),
                            None)
            if not terminal:
                continue
            total_resolved += 1
            automated = terminal["prev_status"]
            decided = "APPROVED" if terminal["new_status"].startswith("APPROVED") else "REJECTED"

            disagreed = (
                (decided in APPROVE_RES and automated in ("PENDING_REVIEW", "REJECTED"))
                or (decided in REJECT_RES and automated in ("APPROVED", "PENDING_INFO", "APPROVED_BY_REVIEWER"))
            )
            if not disagreed:
                continue

            findings = c.execute(
                """SELECT DISTINCT check_name, code FROM case_finding
                    WHERE case_id = ? AND severity >= 2""", (cid,)
            ).fetchall()
            checks = sorted({f["check_name"] for f in findings})
            codes = sorted({f["code"] for f in findings})
            for ck in checks:
                by_check[ck] = by_check.get(ck, 0) + 1
            for cd in codes:
                by_code[cd] = by_code.get(cd, 0) + 1

            case_row = c.execute("SELECT legal_name FROM onboarding_case WHERE case_id = ?",
                                 (cid,)).fetchone()
            overrides.append({
                "case_id": cid, "legal_name": case_row["legal_name"] if case_row else "",
                "automated": automated, "reviewer_decided": decided,
                "direction": "approved despite flag" if decided == "APPROVED"
                             else "rejected despite pass",
                "checks": checks, "note": terminal["note"], "reviewer": terminal["reviewer"],
            })

    return {
        "resolved_cases": total_resolved,
        "override_count": len(overrides),
        "override_rate": round(100 * len(overrides) / total_resolved, 1) if total_resolved else 0.0,
        "by_check": dict(sorted(by_check.items(), key=lambda kv: -kv[1])),
        "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
        "overrides": overrides,
    }


def list_actions(case_id: str) -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM case_action WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
    return [{"action": r["action"], "reviewer": r["reviewer"], "note": r["note"],
             "prev_status": r["prev_status"], "new_status": r["new_status"],
             "created_at": r["created_at"]} for r in rows]


def fail_case(case_id: str, message: str) -> None:
    with get_conn() as c:
        c.execute(
            "UPDATE onboarding_case SET status='ERROR', reviewer_summary=?, completed_at=? "
            "WHERE case_id=?",
            (message, _now(), case_id),
        )


def _col(r, name, default=None):
    """Tolerant column access — a pre-migration row may lack a column."""
    try:
        return r[name]
    except (IndexError, KeyError):
        return default


def _row(r) -> dict[str, Any]:
    return {
        "case_id": r["case_id"], "legal_name": r["legal_name"],
        "trading_name": r["trading_name"], "country": r["country"],
        "contact_email": r["contact_email"], "status": r["status"],
        "reviewer_summary": r["reviewer_summary"], "vendor_email": r["vendor_email"],
        "created_at": r["created_at"], "completed_at": r["completed_at"],
        "revision": _col(r, "revision", 1),
        "supersedes": _col(r, "supersedes"),
        "superseded_by": _col(r, "superseded_by"),
        "resolution": _col(r, "resolution"),
    }


def get_case(case_id: str, full: bool = True) -> Optional[dict[str, Any]]:
    with get_conn() as c:
        r = c.execute("SELECT * FROM onboarding_case WHERE case_id = ?",
                      (case_id,)).fetchone()
        if not r:
            return None
        out = _row(r)
        out["submission"] = json.loads(r["submission"] or "{}")
        cs = _col(r, "change_summary")
        out["change_summary"] = json.loads(cs) if cs else None
        out["actions"] = list_actions(case_id)
        if full:
            out["checks"] = [{
                "seq": s["seq"], "check": s["check_name"], "label": s["label"],
                "summary": s["summary"], "data": json.loads(s["data"]),
                "duration_ms": s["duration_ms"],
            } for s in c.execute(
                "SELECT * FROM case_check WHERE case_id = ? ORDER BY seq", (case_id,))]

            out["findings"] = [{
                "code": f["code"], "severity": f["severity"],
                "severity_name": f["severity_name"], "check": f["check_name"],
                "field": f["field"], "message": f["message"],
                "vendor_message": f["vendor_message"],
                "evidence": json.loads(f["evidence"]),
            } for f in c.execute(
                "SELECT * FROM case_finding WHERE case_id = ? ORDER BY severity DESC, id",
                (case_id,))]
    return out


def list_cases(limit: int = 200, tenant: Optional[str] = None) -> list[dict[str, Any]]:
    with get_conn() as c:
        if tenant:
            rows = c.execute(
                "SELECT * FROM onboarding_case WHERE org_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?", (tenant, limit)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM onboarding_case ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,)).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            counts = c.execute(
                """SELECT severity_name, COUNT(*) n FROM case_finding
                    WHERE case_id = ? GROUP BY severity_name""", (r["case_id"],)
            ).fetchall()
            d["finding_counts"] = {x["severity_name"]: x["n"] for x in counts}
            top = c.execute(
                """SELECT code, message FROM case_finding WHERE case_id = ?
                    ORDER BY severity DESC, id LIMIT 1""", (r["case_id"],)).fetchone()
            d["top_finding"] = dict(top) if top else None
            out.append(d)
    return out


def stats() -> dict[str, Any]:
    with get_conn() as c:
        by = {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) n FROM onboarding_case GROUP BY status")}
        total = sum(by.values())
        avg = c.execute(
            "SELECT AVG(t) a FROM (SELECT SUM(duration_ms) t FROM case_check GROUP BY case_id)"
        ).fetchone()["a"]
        top_codes = [dict(r) for r in c.execute(
            """SELECT code, COUNT(*) n FROM case_finding
                WHERE severity >= 2 GROUP BY code ORDER BY n DESC LIMIT 5""")]

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
