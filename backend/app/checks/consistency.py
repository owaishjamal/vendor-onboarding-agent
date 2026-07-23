"""Cross-field consistency — the check the problem statement is really about.

Every individual field here can be perfectly valid on its own. A GB VAT number
is well-formed. A German IBAN passes mod-97. A bank account name is a real
person's name. Nothing a single-field validator looks at is wrong.

The problem only appears when you put two fields next to each other:

    "We are registered in India"     + a tax ID in UK VAT format
    "Kessler Industrietechnik GmbH"  + a bank account held by "K. Weber"
    "We are a German company"        + a Cyprus IBAN

These are the submissions that "look fine on the surface". They are also the
ones that cost money, because a payment sent to the wrong account holder is
usually gone.

SEVERITY REASONING
    None of these findings are NEEDS_INFO. You do not email a vendor and ask
    "why doesn't your bank account match your company name?" — if it is
    innocent the question is confusing, and if it is not you have just told a
    fraudster which control caught them. All cross-field inconsistencies go to
    an internal reviewer.
"""

from __future__ import annotations

import re
from typing import Any

from backend.app.checks.base import (
    Timer, email_domain, finding, is_free_email, name_score, name_verdict,
    normalise_name,
)

# Tokens that, appended to a company's name, denote a DIFFERENT legal entity
# rather than a spelling variation. A subtle redirection attack names the
# account "<Company> Holdings" or "<Company> Group" — high string similarity,
# but the money goes to a related-but-distinct party. These are the words that
# distinguish "the vendor" from "an entity connected to the vendor".
RELATED_ENTITY_TOKENS = {
    "holdings", "holding", "group", "trading", "international", "global",
    "services", "ventures", "capital", "partners", "associates", "enterprises",
    "management", "consulting", "personal", "private", "family", "trust",
}


def _added_entity_tokens(legal: str, account: str) -> set[str]:
    """Significant tokens the account name adds relative to the legal name.

    Uses a MULTISET difference, not a set difference, so "<Name> Trading Co
    Trading Ltd" is caught even though 'trading' already appears once in the
    legal name — the account has one more. A plain set difference would miss
    exactly the case where the added word collides with an existing token.
    """
    from collections import Counter
    la = Counter(normalise_name(legal).split())
    ac = Counter(normalise_name(account).split())
    added = ac - la          # multiset subtraction: only positive surpluses
    return {tok for tok in added if tok in RELATED_ENTITY_TOKENS}
from backend.app.checks.formats import normalise_iban
from backend.app.models import (
    CheckResult, Finding, FindingCode, Severity, VendorSubmission,
)
from backend.app.rules import country_name, is_supported, load_country_rules

CHECK = "consistency"

# Tax ID prefixes that identify a country. Used to detect the case where a
# vendor claims one country but supplies another country's tax number.
TAX_PREFIX_COUNTRY = {
    "GB": "GB", "DE": "DE", "FR": "FR", "IE": "IE", "NL": "NL", "BE": "BE",
    "ES": "ES", "IT": "IT", "AT": "AT", "PL": "PL", "SE": "SE", "DK": "DK",
    "PT": "PT", "FI": "FI", "CZ": "CZ", "LU": "LU", "GR": "EL", "RO": "RO",
}


def _tax_id_implied_country(tax_id: str) -> str | None:
    """Which country does this tax number look like it belongs to?"""
    s = (tax_id or "").strip().upper()
    if len(s) < 3:
        return None
    prefix = s[:2]
    if prefix.isalpha() and prefix in TAX_PREFIX_COUNTRY and s[2:3].isdigit():
        return prefix
    # A 15-char GSTIN is unmistakably Indian.
    if re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]", s):
        return "IN"
    # NN-NNNNNNN is a US EIN.
    if re.fullmatch(r"\d{2}-\d{7}", s):
        return "US"
    return None


def run(sub: VendorSubmission) -> CheckResult:
    findings: list[Finding] = []
    data: dict[str, Any] = {}

    with Timer() as t:
        claimed = (sub.country or "").strip().upper()

        # ------------------------------------------------------------------
        # 1. Bank account holder vs legal name
        #    The single highest-value check in the whole system. Payment
        #    redirection fraud almost always shows up here first.
        # ------------------------------------------------------------------
        if sub.bank.account_name and sub.legal_name:
            score = name_score(sub.legal_name, sub.bank.account_name)
            verdict = name_verdict(score)
            data["bank_name_score"] = round(score, 1)
            data["bank_name_verdict"] = verdict

            added = _added_entity_tokens(sub.legal_name, sub.bank.account_name)
            data["added_entity_tokens"] = sorted(added)

            if verdict == "MISMATCH":
                findings.append(finding(
                    FindingCode.BANK_NAME_MISMATCH, Severity.NEEDS_REVIEW, CHECK,
                    message=(
                        f"Bank account holder '{sub.bank.account_name}' does not match the "
                        f"registered legal name '{sub.legal_name}' (similarity {score:.0f}%). "
                        f"Paying this vendor would send funds to a differently-named party. "
                        f"Confirm the account holder with a known contact using a phone "
                        f"number you already hold — not one from this submission."
                    ),
                    field="bank.account_name",
                    legal_name=sub.legal_name, account_name=sub.bank.account_name,
                    score=round(score, 1),
                ))
            elif added:
                # High string similarity, but the account name adds a word that
                # denotes a separate entity ("... Holdings", "... Group"). This
                # is the SUBTLE redirection case that a similarity threshold
                # alone waves through. It can be legitimate (group treasury), so
                # it's a review, not a rejection — but it must never auto-pass.
                findings.append(finding(
                    FindingCode.BANK_NAME_MISMATCH, Severity.NEEDS_REVIEW, CHECK,
                    message=(
                        f"Bank account holder '{sub.bank.account_name}' closely matches the "
                        f"legal name but adds '{', '.join(sorted(added))}', which denotes a "
                        f"related but distinct entity rather than the vendor itself "
                        f"({score:.0f}% string similarity). This is the pattern of a payment "
                        f"redirected to an affiliated account — confirm the relationship in "
                        f"writing before paying. Legitimate for group treasury arrangements, "
                        f"but never automatic."
                    ),
                    field="bank.account_name",
                    legal_name=sub.legal_name, account_name=sub.bank.account_name,
                    score=round(score, 1), added_tokens=sorted(added),
                ))
            elif verdict == "PARTIAL":
                findings.append(finding(
                    FindingCode.BANK_NAME_MISMATCH, Severity.ADVISORY, CHECK,
                    message=(f"Bank account holder '{sub.bank.account_name}' is close to but "
                             f"not identical to '{sub.legal_name}' ({score:.0f}%). Likely a "
                             f"trading-name or abbreviation difference."),
                    field="bank.account_name",
                    legal_name=sub.legal_name, account_name=sub.bank.account_name,
                    score=round(score, 1),
                ))

        # ------------------------------------------------------------------
        # 2. IBAN country vs claimed country
        #    A German company banking in Germany is unremarkable. A German
        #    company that suddenly banks somewhere else is worth a look —
        #    legitimate often (group treasury), but never automatic.
        # ------------------------------------------------------------------
        if sub.bank.iban and is_supported(claimed):
            iban = normalise_iban(sub.bank.iban)
            iban_country = iban[:2] if len(iban) >= 2 else None
            data["iban_country"] = iban_country
            expected = ((load_country_rules(claimed).get("bank", {}) or {})
                        .get("iban", {}) or {}).get("country_prefix")

            if iban_country and expected and iban_country != expected:
                findings.append(finding(
                    FindingCode.IBAN_COUNTRY_MISMATCH, Severity.NEEDS_REVIEW, CHECK,
                    message=(
                        f"Vendor is registered in {country_name(claimed)} ({claimed}) but the "
                        f"IBAN is issued in {iban_country}. Cross-border banking is legitimate "
                        f"for group treasury arrangements, but it needs confirming before "
                        f"first payment."
                    ),
                    field="bank.iban",
                    claimed_country=claimed, iban_country=iban_country,
                ))

        # Bank country field disagreeing with the IBAN itself.
        if sub.bank.iban and sub.bank.bank_country:
            iban_country = normalise_iban(sub.bank.iban)[:2]
            stated = sub.bank.bank_country.strip().upper()
            if iban_country and stated and iban_country != stated:
                findings.append(finding(
                    FindingCode.IBAN_COUNTRY_MISMATCH, Severity.NEEDS_REVIEW, CHECK,
                    message=(f"Stated bank country is {stated} but the IBAN is issued in "
                             f"{iban_country}. The submission contradicts itself."),
                    field="bank.bank_country",
                    stated=stated, iban_country=iban_country,
                ))

        # ------------------------------------------------------------------
        # 3. Tax ID country vs claimed country
        # ------------------------------------------------------------------
        if sub.tax_id and claimed:
            implied = _tax_id_implied_country(sub.tax_id)
            data["tax_id_implied_country"] = implied
            if implied and implied != claimed:
                findings.append(finding(
                    FindingCode.TAX_ID_COUNTRY_MISMATCH, Severity.NEEDS_REVIEW, CHECK,
                    message=(
                        f"Vendor claims registration in {country_name(claimed)} ({claimed}), "
                        f"but the tax ID '{sub.tax_id}' is in {implied} format. Either the "
                        f"country is wrong or the tax number belongs to a different entity."
                    ),
                    field="tax_id",
                    claimed_country=claimed, implied_country=implied, tax_id=sub.tax_id,
                ))

        # ------------------------------------------------------------------
        # 4. Address country vs claimed country
        # ------------------------------------------------------------------
        if sub.address_country and claimed:
            addr_c = sub.address_country.strip().upper()
            if addr_c != claimed:
                findings.append(finding(
                    FindingCode.ADDRESS_COUNTRY_MISMATCH, Severity.NEEDS_REVIEW, CHECK,
                    message=(f"Registered country is {claimed} but the address given is in "
                             f"{addr_c}. A vendor may trade from another country, but the "
                             f"registered address should match the country of registration."),
                    field="address_country",
                    claimed_country=claimed, address_country=addr_c,
                ))

        # ------------------------------------------------------------------
        # 5. Contact email domain
        #    Weak signal alone — plenty of legitimate small suppliers use
        #    gmail. Recorded as advisory so it shows on the file and can
        #    corroborate a stronger finding, without blocking on its own.
        # ------------------------------------------------------------------
        if sub.contact_email:
            dom = email_domain(sub.contact_email)
            data["email_domain"] = dom
            if is_free_email(sub.contact_email):
                findings.append(finding(
                    FindingCode.FREE_EMAIL_DOMAIN, Severity.ADVISORY, CHECK,
                    message=(f"Contact email uses the free domain '{dom}' rather than a "
                             f"company domain. Common for small suppliers; only significant "
                             f"alongside other findings."),
                    field="contact_email", domain=dom,
                ))
            elif sub.website and dom:
                site = re.sub(r"^https?://(www\.)?", "", sub.website.strip().lower()).split("/")[0]
                if site and dom != site:
                    score = name_score(dom.split(".")[0], site.split(".")[0])
                    if score < 60:
                        findings.append(finding(
                            FindingCode.EMAIL_DOMAIN_MISMATCH, Severity.ADVISORY, CHECK,
                            message=(f"Contact email domain '{dom}' does not correspond to the "
                                     f"stated website '{site}'."),
                            field="contact_email", email_domain=dom, website_domain=site,
                        ))

    blocking = [f for f in findings if f.severity >= Severity.NEEDS_REVIEW]
    summary = ("All fields are mutually consistent." if not findings
               else f"{len(blocking)} inconsistency requiring review, "
                    f"{len(findings) - len(blocking)} advisory."
               if blocking else f"{len(findings)} advisory note(s); nothing blocking.")

    return CheckResult(check=CHECK, label="Cross-field consistency",
                       findings=findings, summary=summary, duration_ms=t.ms, data=data)
