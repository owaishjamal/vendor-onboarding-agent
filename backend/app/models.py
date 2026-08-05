"""Domain model for vendor onboarding.

THE CENTRAL DIFFERENCE FROM AN INVOICE PIPELINE

    An invoice pipeline stops at the first decisive failure. That is correct
    there: once you know an invoice is a duplicate, nothing else matters.

    Onboarding is the opposite. If a vendor's submission has four problems,
    finding one and stopping means the vendor gets told about one problem,
    fixes it, resubmits, and gets told about the next. Four round trips, four
    days of latency each, and a procurement team living in an email thread.

    So every check here runs to completion and emits FINDINGS. The decision is
    an aggregation over all findings, and the vendor gets one message listing
    everything at once. That is the whole design.

Severity is an ordered enum, and the case status is a pure function of the
highest severity present. There is no separate scoring model to argue with.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field


# ---------------------------------------------------------------------------
# Severity and status
# ---------------------------------------------------------------------------

class Severity(int, Enum):
    """Ordered. The case status is determined by the maximum present.

    The gap between NEEDS_INFO and NEEDS_REVIEW is the important one, and it
    is not about how serious the problem is - it is about WHO CAN FIX IT.

        NEEDS_INFO   - the vendor can resolve this themselves. Something is
                       missing or malformed. Send them a message, wait.
        NEEDS_REVIEW - a human on our side has to make a judgement call.
                       Sending this to the vendor is useless at best and
                       tips off a fraudster at worst.

    A missing tax certificate and a bank account belonging to someone else are
    both "not approved", but routing them to the same place would be a mistake.
    """

    INFO = 0        # recorded, affects nothing
    ADVISORY = 1    # worth noting on the file, does not block
    NEEDS_INFO = 2  # vendor can fix; goes into the vendor email
    NEEDS_REVIEW = 3  # internal human judgement required; never sent to vendor
    REJECT = 4      # terminal; no human needed to say no


class Status(str, Enum):
    APPROVED = "APPROVED"
    PENDING_INFO = "PENDING_INFO"
    PENDING_REVIEW = "PENDING_REVIEW"
    REJECTED = "REJECTED"


SEVERITY_TO_STATUS: dict[Severity, Status] = {
    Severity.INFO: Status.APPROVED,
    Severity.ADVISORY: Status.APPROVED,
    Severity.NEEDS_INFO: Status.PENDING_INFO,
    Severity.NEEDS_REVIEW: Status.PENDING_REVIEW,
    Severity.REJECT: Status.REJECTED,
}


# ---------------------------------------------------------------------------
# Finding codes
# ---------------------------------------------------------------------------

class FindingCode(str, Enum):
    """Closed set. Every finding maps to exactly one of these.

    Closed because an audit that says "rejected for other reasons" is not an
    audit, and because a fixed vocabulary is what lets you count how often each
    failure mode occurs and go fix the upstream cause.
    """

    # --- completeness (vendor-fixable)
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    MISSING_REQUIRED_DOCUMENT = "MISSING_REQUIRED_DOCUMENT"
    WRONG_DOCUMENT_TYPE = "WRONG_DOCUMENT_TYPE"

    # --- format (vendor-fixable)
    TAX_ID_FORMAT_INVALID = "TAX_ID_FORMAT_INVALID"
    REGISTRATION_NUMBER_FORMAT_INVALID = "REGISTRATION_NUMBER_FORMAT_INVALID"
    IBAN_CHECKSUM_FAILED = "IBAN_CHECKSUM_FAILED"
    IBAN_FORMAT_INVALID = "IBAN_FORMAT_INVALID"
    ROUTING_NUMBER_INVALID = "ROUTING_NUMBER_INVALID"
    SWIFT_FORMAT_INVALID = "SWIFT_FORMAT_INVALID"
    EMAIL_FORMAT_INVALID = "EMAIL_FORMAT_INVALID"
    UNSUPPORTED_COUNTRY = "UNSUPPORTED_COUNTRY"

    # --- cross-field consistency (needs a human)
    BANK_NAME_MISMATCH = "BANK_NAME_MISMATCH"
    IBAN_COUNTRY_MISMATCH = "IBAN_COUNTRY_MISMATCH"
    TAX_ID_COUNTRY_MISMATCH = "TAX_ID_COUNTRY_MISMATCH"
    ADDRESS_COUNTRY_MISMATCH = "ADDRESS_COUNTRY_MISMATCH"
    DOCUMENT_NAME_MISMATCH = "DOCUMENT_NAME_MISMATCH"
    EMAIL_DOMAIN_MISMATCH = "EMAIL_DOMAIN_MISMATCH"
    FREE_EMAIL_DOMAIN = "FREE_EMAIL_DOMAIN"

    # --- documents
    DOCUMENT_EXPIRED = "DOCUMENT_EXPIRED"
    DOCUMENT_UNREADABLE = "DOCUMENT_UNREADABLE"
    DOCUMENT_LOW_CONFIDENCE = "DOCUMENT_LOW_CONFIDENCE"
    DOCUMENT_TYPE_MISMATCH = "DOCUMENT_TYPE_MISMATCH"

    # --- external registry verification
    REGISTRY_VERIFIED = "REGISTRY_VERIFIED"
    REGISTRY_NOT_FOUND = "REGISTRY_NOT_FOUND"
    REGISTRY_NAME_MISMATCH = "REGISTRY_NAME_MISMATCH"
    REGISTRY_INACTIVE = "REGISTRY_INACTIVE"

    # --- screening and fraud
    DENIED_PARTY_MATCH = "DENIED_PARTY_MATCH"
    DENIED_PARTY_NEAR_MATCH = "DENIED_PARTY_NEAR_MATCH"
    BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR = "BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR"
    DUPLICATE_VENDOR_REGISTRATION = "DUPLICATE_VENDOR_REGISTRATION"
    DUPLICATE_TAX_ID = "DUPLICATE_TAX_ID"

    # --- evidence-first field verification
    FIELD_CORROBORATED = "FIELD_CORROBORATED"
    FIELD_CONTRADICTED = "FIELD_CONTRADICTED"
    FIELD_UNEVIDENCED = "FIELD_UNEVIDENCED"

    # --- profile custom validation
    CUSTOM_FIELD_INVALID = "CUSTOM_FIELD_INVALID"
    CUSTOM_RULE_FAILED = "CUSTOM_RULE_FAILED"
    SEMANTIC_RULE_FLAGGED = "SEMANTIC_RULE_FLAGGED"

    # --- clean
    ALL_CHECKS_PASSED = "ALL_CHECKS_PASSED"


class Finding(BaseModel):
    """One thing a check noticed.

    `vendor_message` is separate from `message` on purpose. The internal note
    can say "bank account holder differs from legal name - possible payment
    redirection". What we send the vendor must not say that: if it is fraud we
    have tipped them off, and if it is not fraud we have accused a legitimate
    supplier. Vendor-facing text only exists for NEEDS_INFO findings.
    """

    code: FindingCode
    severity: Severity
    check: str
    field: Optional[str] = None
    message: str                                  # internal / reviewer-facing
    vendor_message: Optional[str] = None          # only for NEEDS_INFO
    evidence: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[misc]
    @property
    def severity_name(self) -> str:
        """Always serialised alongside the int severity, so every consumer —
        the live SSE stream and the stored case alike — has the label the UI
        keys on. Without this, streamed findings carried only the int and the
        client crashed indexing on `undefined`."""
        return self.severity.name


class CheckResult(BaseModel):
    check: str
    label: str
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    duration_ms: int = 0
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def max_severity(self) -> Severity:
        return max((f.severity for f in self.findings), default=Severity.INFO)


# ---------------------------------------------------------------------------
# The submission
# ---------------------------------------------------------------------------

class BankDetails(BaseModel):
    account_name: Optional[str] = None
    account_number: Optional[str] = None
    iban: Optional[str] = None
    routing_number: Optional[str] = None      # US ABA
    ifsc: Optional[str] = None                # India
    swift_bic: Optional[str] = None
    bank_name: Optional[str] = None
    bank_country: Optional[str] = None


class SubmittedDocument(BaseModel):
    """A document the vendor attached.

    Two ways the content reaches the pipeline, in priority order:

      1. `path` - a real file on disk (PDF or image). When present, the
         document reader actually opens it: pdf text layer first, OCR fallback
         for scans. This is the production path, and it is what the fixtures
         use - every sample document is a rendered PDF the reader parses for
         real, including one scan that forces OCR.

      2. `extracted` - a pre-parsed field block. Used for submissions that
         arrive as pure JSON with no file attached (e.g. pasted into the UI),
         so the cross-referencing logic still has something to work with. The
         reader treats these as read with full confidence.

    Either way the downstream cross-referencing is identical - which is the
    point of keeping extraction behind its own module.
    """

    doc_type: str                                  # the TYPE THE VENDOR CLAIMS this is
    filename: str
    path: Optional[str] = None                     # real file, relative to data/documents
    readable: bool = True                          # author override; the reader can also set this
    extracted: dict[str, Any] = Field(default_factory=dict)

    # Populated by the document reader at runtime; not part of the submission.
    detected_type: Optional[str] = None
    read_confidence: Optional[float] = None
    read_source: Optional[str] = None              # "text_layer" | "ocr" | "provided" | "none"


class Person(BaseModel):
    name: str
    dob: Optional[str] = None            # YYYY-MM-DD
    nationality: Optional[str] = None    # ISO-3166 alpha-2


class VendorSubmission(BaseModel):
    submission_id: Optional[str] = None
    # Which client Requirement Profile this submission answers. None/"default"
    # = the country-pack behaviour that predates profiles.
    profile_id: Optional[str] = None
    # Values for the profile's custom fields, keyed by FieldSpec.key.
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    legal_name: str = ""
    trading_name: Optional[str] = None
    country: str = ""                          # ISO-3166 alpha-2, claimed by vendor
    entity_type: Optional[str] = None
    registration_number: Optional[str] = None   # CIN / company number
    tax_id: Optional[str] = None                # GSTIN / VAT / EIN
    pan: Optional[str] = None                   # India: PAN
    address_line1: Optional[str] = None
    address_city: Optional[str] = None
    address_postcode: Optional[str] = None
    address_country: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    website: Optional[str] = None
    directors: list[str] = Field(default_factory=list)
    # Optional structured people, when the vendor supplies dates of birth and
    # nationality. Screening uses these as SECONDARY identifiers to confirm or
    # clear a name match — the difference between rejecting a sanctioned person
    # and rejecting an innocent namesake.
    director_details: list["Person"] = Field(default_factory=list)
    bank: BankDetails = Field(default_factory=BankDetails)
    documents: list[SubmittedDocument] = Field(default_factory=list)
    payment_terms: Optional[str] = None


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

class ApprovedVendor(BaseModel):
    """A vendor already on the master file. Used for duplicate detection."""

    vendor_id: str
    legal_name: str
    country: str
    tax_id: Optional[str] = None
    registration_number: Optional[str] = None
    bank_account_fingerprint: Optional[str] = None
    status: str = "ACTIVE"


class DeniedParty(BaseModel):
    """Consolidated denied-party / sanctions entry.

    dob and nationality are the SECONDARY identifiers that turn a name
    coincidence into a confirmed hit or a cleared namesake. Real lists carry
    them precisely because names are not unique.
    """

    name: str
    kind: str            # INDIVIDUAL | ENTITY
    list_name: str       # e.g. OFAC_SDN, UK_HMT, EU_CFSP
    country: Optional[str] = None
    dob: Optional[str] = None            # YYYY-MM-DD
    nationality: Optional[str] = None    # ISO-3166 alpha-2
    aliases: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# The case
# ---------------------------------------------------------------------------

class CaseRecord(BaseModel):
    case_id: str
    legal_name: str
    country: str
    status: Status
    findings: list[Finding] = Field(default_factory=list)
    reviewer_summary: str = ""
    vendor_email: Optional[str] = None
    checks: list[CheckResult] = Field(default_factory=list)
    created_at: str = ""
    completed_at: Optional[str] = None
