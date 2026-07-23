"""Duplicates and shared banking details against the existing vendor master.

The interesting finding here is not "this vendor already exists" — that is
housekeeping. It is **a new vendor supplying bank details that already belong
to a different vendor on the master file**.

That pattern is the signature of invoice redirection fraud: an attacker
onboards a plausible new supplier whose bank account is one they already
control, or worse, points a new vendor record at an existing supplier's
account so payments quietly diverge. Neither is visible from the submission
alone. It only appears when you compare against what you already hold.

WHY THIS IS NEEDS_REVIEW AND NOT REJECT
    Shared bank accounts have legitimate explanations. Group companies run
    treasury through one entity. A parent collects on behalf of subsidiaries.
    A factoring arrangement assigns receivables to a finance house. Rejecting
    automatically would break real supplier relationships. But approving
    automatically is how money leaves. So: never auto-approve, never
    auto-reject, always a human — with the conflicting record attached so the
    reviewer can resolve it in one sitting rather than going hunting.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from backend.app import config
from backend.app.checks.base import Timer, finding, name_score
from backend.app.models import (
    ApprovedVendor, CheckResult, Finding, FindingCode, Severity, VendorSubmission,
)

CHECK = "duplicates"


def bank_fingerprint(iban: Optional[str], account_number: Optional[str],
                     routing: Optional[str]) -> Optional[str]:
    """Stable identifier for a bank account, however it was expressed.

    Hashed rather than stored raw so the fingerprint can be compared and
    logged without spreading account numbers through the audit trail. IBAN
    wins when present because it already encodes the domestic account number.
    """
    if iban:
        basis = re.sub(r"\s+", "", iban).upper()
    elif account_number:
        basis = re.sub(r"\D", "", account_number)
        if routing:
            basis = re.sub(r"\D", "", routing) + ":" + basis
    else:
        return None
    if not basis:
        return None
    return hashlib.sha256(basis.encode()).hexdigest()[:24]


def _load_master() -> list[ApprovedVendor]:
    path = config.SEED_DIR / "vendor_master.json"
    if not path.exists():
        return []
    return [ApprovedVendor(**v) for v in json.loads(path.read_text())]


def _norm_id(v: Optional[str]) -> str:
    return re.sub(r"[\s\-]", "", (v or "")).upper()


def run(sub: VendorSubmission) -> CheckResult:
    findings: list[Finding] = []
    master = _load_master()
    data: dict[str, Any] = {"master_size": len(master)}

    with Timer() as t:
        fp = bank_fingerprint(sub.bank.iban, sub.bank.account_number,
                              sub.bank.routing_number)
        data["bank_fingerprint"] = fp

        # ------------------------------------------------------------------
        # 1. Bank account already held by a different vendor
        # ------------------------------------------------------------------
        if fp:
            for v in master:
                if v.bank_account_fingerprint != fp:
                    continue
                similarity = name_score(sub.legal_name, v.legal_name)
                # Same account AND essentially the same name is a resubmission,
                # not a fraud signal - handled by the duplicate-identity checks
                # below instead.
                if similarity >= 85:
                    continue
                findings.append(finding(
                    FindingCode.BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR,
                    Severity.NEEDS_REVIEW, CHECK,
                    message=(
                        f"The bank account supplied is already registered to '{v.legal_name}' "
                        f"({v.vendor_id}), a different vendor on the master file (name "
                        f"similarity {similarity:.0f}%). This can be legitimate — group "
                        f"treasury, a parent collecting on behalf of a subsidiary, or a "
                        f"factoring arrangement — but it must be confirmed in writing by "
                        f"someone at {v.legal_name} before either vendor is paid."
                    ),
                    field="bank",
                    existing_vendor_id=v.vendor_id, existing_legal_name=v.legal_name,
                    name_similarity=round(similarity, 1), fingerprint=fp,
                ))

        # ------------------------------------------------------------------
        # 2. Same registration number already on file
        # ------------------------------------------------------------------
        reg = _norm_id(sub.registration_number)
        if reg:
            for v in master:
                if _norm_id(v.registration_number) == reg:
                    findings.append(finding(
                        FindingCode.DUPLICATE_VENDOR_REGISTRATION,
                        Severity.NEEDS_REVIEW, CHECK,
                        message=(
                            f"Registration number {sub.registration_number} is already held by "
                            f"'{v.legal_name}' ({v.vendor_id}). This is likely a duplicate "
                            f"vendor record rather than a new supplier — check whether the "
                            f"existing record should be updated instead of a second one created."
                        ),
                        field="registration_number",
                        existing_vendor_id=v.vendor_id, existing_legal_name=v.legal_name,
                        registration_number=sub.registration_number,
                    ))

        # ------------------------------------------------------------------
        # 3. Same tax ID already on file
        # ------------------------------------------------------------------
        tax = _norm_id(sub.tax_id)
        if tax:
            for v in master:
                if _norm_id(v.tax_id) == tax:
                    findings.append(finding(
                        FindingCode.DUPLICATE_TAX_ID, Severity.NEEDS_REVIEW, CHECK,
                        message=(f"Tax ID {sub.tax_id} is already registered to "
                                 f"'{v.legal_name}' ({v.vendor_id})."),
                        field="tax_id",
                        existing_vendor_id=v.vendor_id, existing_legal_name=v.legal_name,
                        tax_id=sub.tax_id,
                    ))

    summary = (f"No conflicts against {len(master)} existing vendors."
               if not findings else
               f"{len(findings)} conflict(s) with existing vendor records.")

    return CheckResult(check=CHECK, label="Duplicate & shared-banking check",
                       findings=findings, summary=summary, duration_ms=t.ms, data=data)
