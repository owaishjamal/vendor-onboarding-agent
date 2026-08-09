"""Is everything we need actually here?

This check never stops at the first gap. It collects every missing field and
every missing document in one pass, because the entire cost of onboarding is
round trips — and a round trip that recovers one field when four are missing
is three round trips wasted.
"""

from __future__ import annotations

from typing import Any

from backend.app.checks.base import Timer, finding
from backend.app.models import (
    CheckResult, Finding, FindingCode, Severity, VendorSubmission,
)
from backend.app.rules import is_supported, load_country_rules, required_documents

CHECK = "completeness"

# Fields required regardless of country. Anything country-specific (a
# Companies House number, a GSTIN) is driven by the rule pack instead.
UNIVERSAL_REQUIRED = [
    ("legal_name", "Registered legal name"),
    ("country", "Country of registration"),
    ("address_line1", "Registered address"),
    ("contact_email", "Contact email address"),
]

BANK_REQUIRED_BY_SCHEME = {
    "iban": [("iban", "IBAN"), ("account_name", "Name on the bank account")],
    "aba": [("routing_number", "ABA routing number"),
            ("account_number", "Bank account number"),
            ("account_name", "Name on the bank account")],
    "ifsc": [("ifsc", "IFSC code"), ("account_number", "Bank account number"),
             ("account_name", "Name on the bank account")],
    "swift_account": [("swift_bic", "SWIFT/BIC"),
                      ("account_number", "Bank account number"),
                      ("account_name", "Name on the bank account")],
}


def run(sub: VendorSubmission) -> CheckResult:
    findings: list[Finding] = []
    missing_fields: list[str] = []
    missing_docs: list[str] = []
    supplied_types: set[str] = set()

    # A profile that extends "blank" is not a vendor-onboarding workflow — an
    # invoice or a prospect has no directors, bank mandate or W-9. In that case
    # completeness enforces ONLY what the profile itself declares (its required
    # fields are owned by custom_rules; its documents are checked below), never
    # the country vendor pack.
    from backend.app.profiles.store import get_profile, resolve_requirements
    _profile = get_profile(sub.profile_id, sub.country, sub.category)
    _vendor_shaped = _profile.extends == "country_defaults"
    _resolved = resolve_requirements(_profile, sub.model_dump(mode="json"))

    if not _vendor_shaped:
        # Non-onboarding workflow: enforce ONLY the profile's own documents.
        with Timer() as t2:
            supplied_types = {d.doc_type for d in sub.documents}
            for spec in _profile.documents:
                if not spec.required or spec.key in supplied_types:
                    continue
                missing_docs.append(spec.label)
                findings.append(finding(
                    FindingCode.MISSING_REQUIRED_DOCUMENT, Severity.NEEDS_INFO, CHECK,
                    message=f"Required document '{spec.key}' ({spec.label}) was not attached.",
                    field=f"documents.{spec.key}",
                    vendor_message=f"{spec.label} is required and was not attached.",
                    doc_type=spec.key, label=spec.label,
                ))
        return CheckResult(
            check=CHECK, label="Completeness", findings=findings,
            summary=("All required documents present." if not missing_docs
                     else f"{len(missing_docs)} required document(s) missing."),
            duration_ms=t2.ms,
            data={"missing_fields": [], "missing_documents": missing_docs,
                  "documents_supplied": sorted(supplied_types),
                  "profile_shape": "custom"},
        )

    with Timer() as t:
        # --- universal fields
        for attr, label in UNIVERSAL_REQUIRED:
            if not (getattr(sub, attr, None) or "").strip():
                missing_fields.append(label)
                findings.append(finding(
                    FindingCode.MISSING_REQUIRED_FIELD, Severity.NEEDS_INFO, CHECK,
                    message=f"Required field '{attr}' is empty.",
                    field=attr,
                    vendor_message=f"{label} is required and was not provided.",
                    label=label,
                ))

        country = (sub.country or "").strip().upper()
        if not is_supported(country):
            # Without a rule pack we do not know what else to ask for. Report
            # what we found so far and let the format check raise the
            # unsupported-country finding.
            return CheckResult(
                check=CHECK, label="Completeness", findings=findings,
                summary=(f"{len(missing_fields)} required field(s) missing; "
                         f"country-specific requirements unknown."),
                duration_ms=t.ms,
                data={"missing_fields": missing_fields, "missing_documents": []},
            )

        rules = load_country_rules(country)

        # --- country-specific identifiers
        tax_spec = rules.get("tax_id", {})
        if tax_spec and not (sub.tax_id or "").strip():
            label = tax_spec.get("label", "Tax registration number")
            missing_fields.append(label)
            findings.append(finding(
                FindingCode.MISSING_REQUIRED_FIELD, Severity.NEEDS_INFO, CHECK,
                message=f"{label} is required for {country} and was not provided.",
                field="tax_id",
                vendor_message=(f"{label} is required for vendors registered in "
                                f"{rules.get('country_name', country)}."),
                label=label,
            ))

        reg_spec = rules.get("registration_number", {})
        if reg_spec.get("required") and not (sub.registration_number or "").strip():
            label = reg_spec.get("label", "Company registration number")
            missing_fields.append(label)
            findings.append(finding(
                FindingCode.MISSING_REQUIRED_FIELD, Severity.NEEDS_INFO, CHECK,
                message=f"{label} is required for {country} and was not provided.",
                field="registration_number",
                vendor_message=(f"{label} is required for vendors registered in "
                                f"{rules.get('country_name', country)}."),
                label=label,
            ))

        # --- bank fields, driven by the country's payment scheme
        scheme = (rules.get("bank", {}) or {}).get("scheme")
        for attr, label in BANK_REQUIRED_BY_SCHEME.get(scheme, []):
            if not (getattr(sub.bank, attr, None) or "").strip():
                missing_fields.append(label)
                findings.append(finding(
                    FindingCode.MISSING_REQUIRED_FIELD, Severity.NEEDS_INFO, CHECK,
                    message=f"Bank field '{attr}' is required for {country} ({scheme}).",
                    field=f"bank.{attr}",
                    vendor_message=f"{label} is required so we can pay you.",
                    label=label, scheme=scheme,
                ))

        # --- documents
        # Completeness only asks: is a document ATTACHED for each required slot?
        # Whether the attached file is actually the right *kind* of document is
        # decided in the `documents` check, which reads the file itself — this
        # check has no access to the content and must not guess from metadata.
        supplied_types = {d.doc_type for d in sub.documents}

        # The resolved profile is the single source of truth for what this
        # vendor owes us: country baseline, plus whatever their category adds,
        # plus any conditional item their own answers triggered. It already
        # includes the country documents, so we iterate it rather than
        # `required_documents(country)` — otherwise a category could never
        # mark a country default not-applicable (an individual has no
        # certificate of incorporation).
        for item in _resolved["documents"]:
            if item["effective"] != "required" or item["key"] in supplied_types:
                continue
            missing_docs.append(item["label"])
            why = f" {item['why']}" if item.get("why") else ""
            because = ""
            if item["declared"] == "conditional" and item.get("when_explained"):
                because = f" (required because {item['when_explained']})"
            findings.append(finding(
                FindingCode.MISSING_REQUIRED_DOCUMENT, Severity.NEEDS_INFO, CHECK,
                message=(f"Required document '{item['key']}' ({item['label']}) "
                         f"was not attached{because}."),
                field=f"documents.{item['key']}",
                vendor_message=f"{item['label']} is required and was not attached.{why}",
                doc_type=item["key"], label=item["label"],
                requirement=item["declared"], condition=item.get("when"),
            ))

    n = len(missing_fields) + len(missing_docs)
    summary = ("Submission is complete — all required fields and documents present."
               if n == 0 else
               f"{len(missing_fields)} field(s) and {len(missing_docs)} document(s) missing.")

    return CheckResult(
        check=CHECK, label="Completeness", findings=findings, summary=summary,
        duration_ms=t.ms,
        data={"missing_fields": missing_fields, "missing_documents": missing_docs,
              "documents_supplied": sorted(supplied_types),
              "category": sub.category,
              # The full resolved checklist, including items that came out
              # not-applicable. Ops needs to see what was considered and
              # dismissed, not just what is outstanding.
              "requirements": _resolved},
    )
