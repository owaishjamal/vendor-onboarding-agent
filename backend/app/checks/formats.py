"""Format validation, including real checksum arithmetic.

Regex alone tells you a value has the right SHAPE. Checksums tell you it could
actually exist. That distinction matters here: a transposed digit in an IBAN
produces a string that passes every regex you could write and still fails
mod-97. Catching it at onboarding costs one email; missing it means a failed
payment, a chase, and a vendor who thinks you are incompetent.

Two algorithms are implemented properly rather than approximated:

  * ISO 13616 IBAN check digits (mod-97-10)
  * ABA routing number checksum (weighted 3-7-1)

Both are cheap, both are standards, and both catch typos that shape-checking
cannot.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.app.checks.base import EMAIL_RE, Timer, finding
from backend.app.models import (
    CheckResult, Finding, FindingCode, Severity, VendorSubmission,
)
from backend.app.rules import is_supported, load_country_rules

CHECK = "formats"


# ---------------------------------------------------------------------------
# IBAN — ISO 13616 mod-97-10
# ---------------------------------------------------------------------------

# Length per country. IBAN length is fixed per country, so a GB IBAN of 21
# characters is wrong regardless of what the checksum says.
IBAN_LENGTHS = {
    "GB": 22, "DE": 22, "FR": 27, "ES": 24, "IT": 27, "NL": 18, "BE": 16,
    "IE": 22, "PT": 25, "AT": 20, "CH": 21, "SE": 24, "DK": 18, "NO": 15,
    "FI": 18, "PL": 28, "CZ": 24, "LU": 20, "GR": 27, "RO": 24,
}


def normalise_iban(iban: str) -> str:
    return re.sub(r"\s+", "", (iban or "")).upper()


def iban_is_valid(iban: str) -> tuple[bool, str]:
    """Return (valid, reason). Implements the mod-97-10 check digit algorithm.

    Move the first four characters to the end, map each letter to two digits
    (A=10 ... Z=35), interpret the result as a base-10 integer, and require
    that it is congruent to 1 modulo 97.
    """
    s = normalise_iban(iban)
    if not s:
        return False, "empty"
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False, "does not match the general IBAN structure"

    country = s[:2]
    expected = IBAN_LENGTHS.get(country)
    if expected and len(s) != expected:
        return False, f"length {len(s)} but {country} IBANs are {expected} characters"

    rearranged = s[4:] + s[:4]
    digits = "".join(
        str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged
    )
    # Chunked modulo, so we never build an unbounded integer.
    remainder = 0
    for i in range(0, len(digits), 7):
        remainder = int(str(remainder) + digits[i:i + 7]) % 97
    if remainder != 1:
        return False, "check digits are invalid (mod-97 failed)"
    return True, "valid"


# ---------------------------------------------------------------------------
# ABA routing number — weighted 3-7-1 checksum
# ---------------------------------------------------------------------------

def aba_is_valid(routing: str) -> tuple[bool, str]:
    s = re.sub(r"\D", "", routing or "")
    if len(s) != 9:
        return False, f"must be exactly 9 digits, got {len(s)}"
    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    total = sum(int(d) * w for d, w in zip(s, weights))
    if total % 10 != 0:
        return False, "checksum failed (weighted 3-7-1 sum is not divisible by 10)"
    return True, "valid"


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def _regex_field(value: Optional[str], spec: dict[str, Any], code: FindingCode,
                 field: str, findings: list[Finding]) -> None:
    """Validate one field against its rule-pack regex."""
    if not value or not spec or not spec.get("regex"):
        return
    if re.fullmatch(spec["regex"], value.strip()):
        return
    label = spec.get("label", field)
    example = spec.get("example")
    hint = spec.get("hint") or (
        f"Expected format like {example}." if example else "Please check the format."
    )
    findings.append(finding(
        code, Severity.NEEDS_INFO, CHECK,
        message=f"{label} '{value}' does not match the required format for this country.",
        field=field,
        vendor_message=f"The {label} you provided ('{value}') doesn't look right. {hint}",
        provided=value, pattern=spec["regex"], expected_example=example,
    ))


def run(sub: VendorSubmission) -> CheckResult:
    findings: list[Finding] = []
    data: dict[str, Any] = {}

    with Timer() as t:
        country = (sub.country or "").strip().upper()

        if not is_supported(country):
            # We cannot validate anything country-specific, so we must not
            # imply that we did. Straight to a human.
            findings.append(finding(
                FindingCode.UNSUPPORTED_COUNTRY, Severity.NEEDS_REVIEW, CHECK,
                message=(f"No rule pack exists for country '{country or 'unspecified'}', "
                         f"so tax ID, registration and bank formats cannot be validated."),
                field="country", country=country,
            ))
            return CheckResult(
                check=CHECK, label="Format validation", findings=findings,
                summary=f"Country '{country or '—'}' is not supported; cannot validate formats.",
                duration_ms=t.ms if hasattr(t, "ms") else 0, data={"country": country},
            )

        rules = load_country_rules(country)

        # --- tax id / registration number
        _regex_field(sub.tax_id, rules.get("tax_id", {}),
                     FindingCode.TAX_ID_FORMAT_INVALID, "tax_id", findings)
        _regex_field(sub.registration_number, rules.get("registration_number", {}),
                     FindingCode.REGISTRATION_NUMBER_FORMAT_INVALID,
                     "registration_number", findings)

        # --- bank details, by scheme
        bank_rules = rules.get("bank", {}) or {}
        scheme = bank_rules.get("scheme")
        data["bank_scheme"] = scheme

        if scheme == "iban" and sub.bank.iban:
            ok, reason = iban_is_valid(sub.bank.iban)
            data["iban_valid"] = ok
            data["iban_reason"] = reason
            if not ok:
                # A failed checksum is overwhelmingly a typo, not fraud. It is
                # vendor-fixable, so it goes in the email rather than to a
                # reviewer. Getting this triage right is the difference between
                # a one-line correction and an accusation.
                code = (FindingCode.IBAN_CHECKSUM_FAILED
                        if "mod-97" in reason else FindingCode.IBAN_FORMAT_INVALID)
                findings.append(finding(
                    code, Severity.NEEDS_INFO, CHECK,
                    message=f"IBAN '{sub.bank.iban}' failed validation: {reason}.",
                    field="bank.iban",
                    vendor_message=(
                        f"The IBAN you provided ({sub.bank.iban}) isn't valid — {reason}. "
                        f"This is usually a typo. Please check it against your bank "
                        f"statement and resend."
                    ),
                    iban=sub.bank.iban, reason=reason,
                ))

        if scheme == "aba" and sub.bank.routing_number:
            ok, reason = aba_is_valid(sub.bank.routing_number)
            data["routing_valid"] = ok
            data["routing_reason"] = reason
            if not ok:
                findings.append(finding(
                    FindingCode.ROUTING_NUMBER_INVALID, Severity.NEEDS_INFO, CHECK,
                    message=f"Routing number '{sub.bank.routing_number}' is invalid: {reason}.",
                    field="bank.routing_number",
                    vendor_message=(
                        f"The routing number you provided ({sub.bank.routing_number}) "
                        f"isn't valid — {reason}. Please check it and resend."
                    ),
                    routing_number=sub.bank.routing_number, reason=reason,
                ))

        # Account number shape, where the pack specifies one.
        acct_spec = bank_rules.get("account_number", {})
        if acct_spec.get("regex") and sub.bank.account_number:
            if not re.fullmatch(acct_spec["regex"], sub.bank.account_number.strip()):
                findings.append(finding(
                    FindingCode.ROUTING_NUMBER_INVALID, Severity.NEEDS_INFO, CHECK,
                    message=(f"Account number '{sub.bank.account_number}' does not match "
                             f"the expected pattern for {country}."),
                    field="bank.account_number",
                    vendor_message=("The bank account number doesn't match the expected "
                                    "format for your country. Please check it and resend."),
                    provided=sub.bank.account_number,
                ))

        # SWIFT/BIC, where present.
        swift_spec = bank_rules.get("swift_bic", {})
        if swift_spec.get("regex") and sub.bank.swift_bic:
            if not re.fullmatch(swift_spec["regex"], sub.bank.swift_bic.strip().upper()):
                findings.append(finding(
                    FindingCode.SWIFT_FORMAT_INVALID, Severity.NEEDS_INFO, CHECK,
                    message=f"SWIFT/BIC '{sub.bank.swift_bic}' is not a valid BIC.",
                    field="bank.swift_bic",
                    vendor_message=(f"The SWIFT/BIC code ({sub.bank.swift_bic}) isn't valid. "
                                    f"It should be 8 or 11 characters, e.g. BARCGB22."),
                    provided=sub.bank.swift_bic,
                ))

        # --- contact email
        if sub.contact_email and not EMAIL_RE.match(sub.contact_email.strip()):
            findings.append(finding(
                FindingCode.EMAIL_FORMAT_INVALID, Severity.NEEDS_INFO, CHECK,
                message=f"Contact email '{sub.contact_email}' is not a valid address.",
                field="contact_email",
                vendor_message="The contact email address doesn't look valid. Please correct it.",
                provided=sub.contact_email,
            ))

    checked = [f for f in ("tax_id", "registration_number", "bank", "email")]
    summary = ("All provided identifiers are correctly formatted."
               if not findings else
               f"{len(findings)} formatting problem(s) found.")

    return CheckResult(check=CHECK, label="Format validation", findings=findings,
                       summary=summary, duration_ms=t.ms, data=data)
