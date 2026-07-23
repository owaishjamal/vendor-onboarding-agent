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
        supplied_types = {d.doc_type for d in sub.documents}
        supplied_kinds = {
            (d.extracted.get("kind") or d.doc_type) for d in sub.documents
        }

        for spec in required_documents(country):
            dtype = spec["doc_type"]
            if dtype in supplied_types:
                # Present, but is it the right kind of document? Vendors
                # routinely attach a delivery note where a bank letter belongs.
                accepted = set(spec.get("accepted", []))
                if accepted and not (supplied_kinds & accepted):
                    got = next((d for d in sub.documents if d.doc_type == dtype), None)
                    findings.append(finding(
                        FindingCode.WRONG_DOCUMENT_TYPE, Severity.NEEDS_INFO, CHECK,
                        message=(f"A document was supplied for '{dtype}' but it appears "
                                 f"to be '{got.extracted.get('kind') if got else 'unknown'}', "
                                 f"not one of {sorted(accepted)}."),
                        field=f"documents.{dtype}",
                        vendor_message=(f"The file you attached for {spec['label']} doesn't "
                                        f"appear to be the right document. Please attach "
                                        f"{spec['label'].lower()}."),
                        expected=sorted(accepted),
                        received=(got.extracted.get("kind") if got else None),
                        filename=(got.filename if got else None),
                    ))
                continue

            missing_docs.append(spec["label"])
            findings.append(finding(
                FindingCode.MISSING_REQUIRED_DOCUMENT, Severity.NEEDS_INFO, CHECK,
                message=f"Required document '{dtype}' ({spec['label']}) was not attached.",
                field=f"documents.{dtype}",
                vendor_message=f"{spec['label']} is required and was not attached.",
                doc_type=dtype, label=spec["label"],
            ))

    n = len(missing_fields) + len(missing_docs)
    summary = ("Submission is complete — all required fields and documents present."
               if n == 0 else
               f"{len(missing_fields)} field(s) and {len(missing_docs)} document(s) missing.")

    return CheckResult(
        check=CHECK, label="Completeness", findings=findings, summary=summary,
        duration_ms=t.ms,
        data={"missing_fields": missing_fields, "missing_documents": missing_docs,
              "documents_supplied": sorted(supplied_types)},
    )
