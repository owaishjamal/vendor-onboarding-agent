"""External registry verification — does this company actually exist?

Every other check confirms the submission agrees WITH ITSELF: the tax ID is
well-formatted, the name on the document matches the form, the fields are
mutually consistent. None of that proves the entity is real. A fraudster who
invents a company can make every internal check pass — a plausible name, a
correctly-formatted registration number, matching documents they authored.

This check is the one that looks OUTSIDE the submission. It confirms the
registration number against a registry the vendor does not control, and:

  * NOT FOUND        -> we cannot confirm the company exists. Escalate. This is
                       the case that stops a fabricated-but-consistent vendor
                       from being auto-approved.
  * NAME MISMATCH    -> the number is real but registered to a different
                       company. Escalate — possible identity borrowing.
  * INACTIVE         -> the company exists but is dissolved/struck off.
  * VERIFIED         -> exists, active, name matches. Recorded positively.

In production the registry is Companies House / Handelsregister / an aggregator
API. Here it is a seeded file, but the check's shape — and the fact that a
vendor we can't verify does not sail through — is the real point.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from backend.app import config
from backend.app.checks.base import Timer, finding, name_score, name_verdict
from backend.app.models import (
    CheckResult, Finding, FindingCode, Severity, VendorSubmission,
)
from backend.app.providers.registry_provider import (
    get_registry_provider, set_registry_override,
)
from backend.app.rules import is_supported

CHECK = "registry"

# Re-exported so existing callers (e.g. the evaluator) keep importing it from here.
__all__ = ["run", "set_registry_override"]


def _norm(v: Optional[str]) -> str:
    import re
    return re.sub(r"[\s\-]", "", (v or "")).upper()


def run(sub: VendorSubmission) -> CheckResult:
    findings: list[Finding] = []
    data: dict[str, Any] = {}
    summary = ""

    with Timer() as t:
        country = (sub.country or "").strip().upper()
        reg = _norm(sub.registration_number)
        data["registration_number"] = sub.registration_number

        # Nothing to verify — completeness owns "missing registration", and the
        # format check owns an unsupported country.
        if not reg or not is_supported(country):
            summary = ("No registration number to verify against the registry."
                       if not reg else "Country not supported for registry lookup.")
        else:
            provider = get_registry_provider()
            data["registry_source"] = getattr(provider, "source", "seed")
            match = provider.lookup(country, reg)

            if match is None:
                findings.append(finding(
                    FindingCode.REGISTRY_NOT_FOUND, Severity.NEEDS_REVIEW, CHECK,
                    message=(
                        f"Registration number {sub.registration_number} could not be found "
                        f"in the {country} company registry. The submission is internally "
                        f"consistent, but the company's existence cannot be confirmed — a "
                        f"reviewer should verify it against the registry directly before "
                        f"onboarding. This is the check a fabricated vendor fails."
                    ),
                    field="registration_number",
                    registration_number=sub.registration_number, country=country,
                ))
                summary = f"{sub.registration_number} not found in the {country} registry."
            else:
                data["registry_record"] = {
                    "legal_name": match.legal_name, "status": match.status,
                    "incorporation_date": match.incorporation_date, "source": match.source,
                }
                status = (match.status or "ACTIVE").upper()
                score = name_score(sub.legal_name, match.legal_name or "")
                data["name_score"] = round(score, 1)

                if name_verdict(score) == "MISMATCH":
                    findings.append(finding(
                        FindingCode.REGISTRY_NAME_MISMATCH, Severity.NEEDS_REVIEW, CHECK,
                        message=(
                            f"Registration number {sub.registration_number} is registered to "
                            f"'{match.legal_name}' in the registry, not "
                            f"'{sub.legal_name}' as submitted ({score:.0f}% similar). Either "
                            f"the number belongs to a different company or the vendor is "
                            f"using someone else's registration."
                        ),
                        field="registration_number",
                        submitted_name=sub.legal_name,
                        registry_name=match.legal_name, score=round(score, 1),
                    ))
                    summary = f"Registration belongs to a different company ({match.legal_name})."
                elif status != "ACTIVE":
                    findings.append(finding(
                        FindingCode.REGISTRY_INACTIVE, Severity.NEEDS_REVIEW, CHECK,
                        message=(
                            f"'{sub.legal_name}' exists in the {country} registry but its "
                            f"status is '{status}', not active. A dissolved or struck-off "
                            f"company cannot be onboarded as a going concern — confirm the "
                            f"vendor's current standing."
                        ),
                        field="registration_number", registry_status=status,
                    ))
                    summary = f"Company is on the registry but {status.lower()}, not active."
                else:
                    findings.append(finding(
                        FindingCode.REGISTRY_VERIFIED, Severity.INFO, CHECK,
                        message=(f"'{sub.legal_name}' verified against the {country} "
                                 f"registry ({match.source}): active, registered "
                                 f"{match.incorporation_date or 'n/a'}, name matches at "
                                 f"{score:.0f}%."),
                        field="registration_number",
                        registry_name=match.legal_name, status=status,
                        incorporation_date=match.incorporation_date,
                    ))
                    summary = f"Verified against the {country} registry — active and name matches."

    return CheckResult(check=CHECK, label="Registry verification", findings=findings,
                       summary=summary, duration_ms=t.ms, data=data)
