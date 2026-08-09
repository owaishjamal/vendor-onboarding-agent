"""Demonstrable scenarios — the happy paths and the edge cases, as data.

WHY THIS EXISTS
    A reviewer evaluating this system should not have to invent a sanctioned
    director or hand-type a colliding bank account to see what happens. Every
    interesting behaviour is one click away from the form, prefilled with data
    that genuinely triggers the path it claims to trigger.

    Nothing here is a mock. A scenario is an ordinary set of form values. It
    goes through the same endpoint, the same nine checks and the same decision
    rule as anything typed by hand. The verdicts below are PREDICTIONS, not
    scripted outcomes, and `tests/test_scenarios.py` fails the build if the
    pipeline ever stops agreeing with them.

THE FOUR EDGE CASES, AND WHY THESE FOUR
    An edge case is only interesting if it is a case where the obvious rule
    gives the wrong answer. Each of these breaks a different obvious rule:

    1. shared-bank-account
       Obvious rule: "every field valid → approve."
       Why it fails: nothing is wrong with the submission. The problem is
       invisible from inside it — the bank account already belongs to a
       different vendor. This is the signature of invoice-redirection fraud,
       and it is only detectable by comparing against what you already hold.
       Deliberately NOT a rejection: group treasury and factoring are real and
       legitimate. Auto-rejecting breaks real suppliers; auto-approving is how
       money leaves. So: a human, every time.

    2. sanctions-namesake
       Obvious rule: "name matches a sanctions list → reject."
       Why it fails: names are not unique. This director's name matches an
       OFAC entry exactly, but his date of birth and nationality clear him.
       Screening on names alone means rejecting innocent people, which is both
       a commercial loss and a fairness problem. Secondary identifiers are
       what turn a coincidence into a decision. Contrast with
       `sanctions-confirmed`, where the same machinery rejects outright.

    3. sole-trader-no-incorporation
       Obvious rule: "vendors must supply a certificate of incorporation."
       Why it fails: an individual professional has no such certificate, and
       no amount of asking will produce one. Demanding it is the single most
       common reason good freelancers abandon onboarding. The requirement is
       waived by the category profile — as data, not as a special case in
       code — and the government ID stands in. Also shows the tax certificate
       going away entirely when no tax ID is supplied, because many
       independents fall below the registration threshold.

    4. licence-expiring-soon
       Obvious rule: "expired document → block; valid document → pass."
       Why it fails: a document that is valid today but expires in three weeks
       is neither. Blocking is how procurement gets bypassed by people with a
       deadline. Waving it through is how a vendor ends up transacting on a
       lapsed licence. It becomes APPROVED_WITH_CONDITIONS: onboard now, with
       the renewal recorded against the vendor and chased before the date.

    Together they cover the four distinct shapes a verdict can take —
    NEEDS_REVIEW on clean data, APPROVED despite an alarming signal, APPROVED
    on a reduced requirement set, and APPROVED_WITH_CONDITIONS — which is why
    these four and not four variations on "a field is missing".

DOCUMENTS
    `documents` lists specs the renderer turns into real PDFs, so a scenario
    exercises document reading rather than skipping it. Empty means the
    scenario is about the form, and the missing-document findings are part of
    the point.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# Dates are computed relative to today so a scenario never rots. A fixture
# with a hardcoded 2026 expiry silently stops demonstrating anything the year
# after it was written.
_TODAY = date.today()


def _in_days(n: int) -> str:
    return str(_TODAY + timedelta(days=n))


def _months_ago(n: int) -> str:
    return str(_TODAY - timedelta(days=30 * n))


SCENARIOS: list[dict[str, Any]] = [

    # =====================================================================
    # HAPPY PATHS
    # =====================================================================
    {
        "id": "clean-goods",
        "kind": "happy",
        "label": "Clean goods supplier",
        "blurb": "Everything present, consistent and corroborated by documents.",
        "expect": "APPROVED",
        "expect_why": "No finding above ADVISORY, so the severity rule lands on APPROVED.",
        "teaches": "The baseline. Nine checks, no findings, straight through with no human involved.",
        "category": "goods",
        "form": {
            "legal_name": "Sundara Textiles Private Limited",
            "country": "IN",
            "entity_type": "PRIVATE_LIMITED",
            "registration_number": "U17291TN2016PTC112884",
            "tax_id": "33AAPFS4321F1ZP",
            "pan": "AAPFS4321F",
            "address_line1": "14 Mount Poonamallee Road",
            "address_city": "Chennai",
            "address_postcode": "600056",
            "address_country": "IN",
            "contact_name": "Lakshmi Raman",
            "contact_email": "l.raman@sundaratextiles.in",
            "website": "https://sundaratextiles.in",
            "business_description": "Woven cotton and blended fabric supplied by the roll to garment manufacturers.",
            "directors": ["Lakshmi Raman"],
        },
        "bank": {
            "account_name": "Sundara Textiles Private Limited",
            "account_number": "914010029385712",
            "ifsc": "HDFC0000123",
            "bank_name": "HDFC Bank",
            "bank_country": "IN",
        },
        "custom_fields": {
            "nature_of_goods": "Woven cotton and blended fabric supplied by the roll.",
            "hsn_codes": "5208, 5209",
        },
        "documents": [
            {"doc_type": "tax_form", "filename": "gst_sundara.pdf",
             "extracted": {"kind": "gst_certificate", "legal_name": "Sundara Textiles Private Limited",
                           "number": "33AAPFS4321F1ZP", "issue_date": _months_ago(20)}},
            {"doc_type": "pan_card", "filename": "pan_sundara.pdf",
             "extracted": {"kind": "pan_card", "legal_name": "Sundara Textiles Private Limited",
                           "number": "AAPFS4321F", "issue_date": _months_ago(60)}},
            {"doc_type": "incorporation", "filename": "coi_sundara.pdf",
             "extracted": {"kind": "certificate_of_incorporation",
                           "legal_name": "Sundara Textiles Private Limited",
                           "number": "U17291TN2016PTC112884", "issue_date": "2016-06-22"}},
            {"doc_type": "bank_proof", "filename": "cheque_sundara.pdf",
             "extracted": {"kind": "cancelled_cheque",
                           "account_name": "Sundara Textiles Private Limited",
                           "account_number": "914010029385712", "issue_date": _months_ago(1)}},
        ],
    },
    {
        "id": "incomplete-services",
        "kind": "happy",
        "label": "Missing paperwork",
        "blurb": "A services vendor who left out the bank proof and the PAN card.",
        "expect": "PENDING_INFO",
        "expect_why": "Missing required documents are NEEDS_INFO — fixable by the vendor without a reviewer.",
        "teaches": ("The difference between 'we need more from you' and 'a human must look'. "
                    "This is the only verdict where the vendor is told the full list, in one message "
                    "rather than one item per round trip."),
        "category": "services",
        "form": {
            "legal_name": "Brightpath Consulting Services LLP",
            "country": "IN",
            "entity_type": "LLP",
            "registration_number": "U74999MH2015PTC269898",
            "tax_id": "27AAPFB1234C1ZK",
            "pan": "AAPFB1234C",
            "address_line1": "Unit 402, Trade Centre, Bandra Kurla Complex",
            "address_city": "Mumbai",
            "address_postcode": "400051",
            "address_country": "IN",
            "contact_name": "Devika Nair",
            "contact_email": "d.nair@brightpathconsulting.in",
            "business_description": "Management consulting and process improvement for mid-market manufacturers.",
            "directors": ["Devika Nair"],
        },
        "bank": {
            "account_name": "Brightpath Consulting Services LLP",
            "account_number": "50100234567890",
            "ifsc": "ICIC0000456",
            "bank_name": "ICICI Bank",
            "bank_country": "IN",
        },
        "custom_fields": {
            "service_description": "Management consulting and process improvement for mid-market manufacturers.",
            "engagement_model": "Time & materials",
            "data_access": "No",
        },
        # Deliberately short: no bank_proof, no pan_card.
        "documents": [
            {"doc_type": "tax_form", "filename": "gst_brightpath.pdf",
             "extracted": {"kind": "gst_certificate", "legal_name": "Brightpath Consulting Services LLP",
                           "number": "27AAPFB1234C1ZK", "issue_date": _months_ago(14)}},
            {"doc_type": "incorporation", "filename": "coi_brightpath.pdf",
             "extracted": {"kind": "certificate_of_incorporation",
                           "legal_name": "Brightpath Consulting Services LLP",
                           "number": "U74999MH2015PTC269898", "issue_date": "2015-03-11"}},
        ],
    },

    # =====================================================================
    # EDGE CASE 1 — clean submission, fraudulent pattern
    # =====================================================================
    {
        "id": "shared-bank-account",
        "kind": "edge",
        "label": "Bank account already belongs to another vendor",
        "blurb": ("Every field is valid and every document corroborates it. The account "
                  "number is one already on the master file under a different company."),
        "expect": "PENDING_REVIEW",
        "expect_why": ("duplicates raises BANK_ACCOUNT_SHARED_WITH_OTHER_VENDOR at NEEDS_REVIEW. "
                       "Nothing else fires, so the verdict rests entirely on a signal no "
                       "field-level validation could see."),
        "teaches": (
            "Validation is not verification. Every check that looks INSIDE the submission "
            "passes — the fraud is only visible by comparing against records we already hold. "
            "It is deliberately not a rejection: group treasury, a parent collecting for a "
            "subsidiary and factoring arrangements all produce this exact pattern legitimately. "
            "Auto-rejecting breaks real suppliers; auto-approving is how invoice-redirection "
            "fraud succeeds. So a human decides, with the conflicting record attached."
        ),
        "category": "logistics",
        "form": {
            "legal_name": "Continental Freight Services LLC",
            "country": "US",
            "entity_type": "LLC",
            "registration_number": "C7710455",
            "tax_id": "31-8827104",
            "address_line1": "2255 Corporate Drive",
            "address_city": "Memphis, TN",
            "address_postcode": "38118",
            "address_country": "US",
            "contact_name": "Marcus Hale",
            "contact_email": "m.hale@continentalfreightsvc.com",
            "website": "https://continentalfreightsvc.com",
            "business_description": "Long-haul road freight and regional distribution across the southeastern United States.",
            "directors": ["Marcus Hale"],
        },
        # This exact account+routing pair hashes to a145f735d3240c03b908ec5a,
        # which backend/seed/vendor_master.json already holds against V-2001
        # "Atlas Haulage Group Inc". The collision is real, not asserted.
        "bank": {
            "account_name": "Continental Freight Services LLC",
            "account_number": "770154288",
            "routing_number": "121000031",
            "bank_name": "Mid-South National Bank",
            "bank_country": "US",
        },
        "custom_fields": {
            "fleet_size": 48,
            "service_region": "South",
            "warehousing": "No",
        },
        "documents": [
            {"doc_type": "tax_form", "filename": "w9_continental.pdf",
             "extracted": {"kind": "w9", "legal_name": "Continental Freight Services LLC",
                           "number": "31-8827104", "issue_date": _months_ago(4)}},
            {"doc_type": "bank_proof", "filename": "voided_cheque_continental.pdf",
             "extracted": {"kind": "voided_cheque", "account_name": "Continental Freight Services LLC",
                           "account_number": "770154288", "issue_date": _months_ago(2)}},
            {"doc_type": "transit_insurance", "filename": "transit_policy_continental.pdf",
             "extracted": {"kind": "insurance_certificate", "legal_name": "Continental Freight Services LLC",
                           "issue_date": _months_ago(3), "expiry_date": _in_days(280)}},
            # fleet_size > 0, so the carrier licence is asked for.
            {"doc_type": "carrier_licence", "filename": "carrier_licence_continental.pdf",
             "extracted": {"kind": "licence", "legal_name": "Continental Freight Services LLC",
                           "number": "MC-884120", "issue_date": _months_ago(9),
                           "expiry_date": _in_days(320)}},
        ],
    },

    # =====================================================================
    # EDGE CASE 2 — the alarming signal that should NOT stop anyone
    # =====================================================================
    {
        "id": "sanctions-namesake",
        "kind": "edge",
        "label": "Director shares a name with a sanctioned individual",
        "blurb": ("The director's name matches an OFAC entry exactly. His date of birth and "
                  "nationality do not."),
        "expect": "APPROVED",
        "expect_why": ("screening finds a 100% name match, then clears it on secondary "
                       "identifiers. The finding is recorded at INFO/ADVISORY so the reasoning "
                       "is auditable, but it does not gate the decision."),
        "teaches": (
            "A false positive is a real cost, not a safe default. Screening on names alone "
            "means turning away legitimate suppliers because someone shares a surname with a "
            "designated person — common with transliterated names, and a fairness problem as "
            "much as a commercial one. Secondary identifiers are what make screening a "
            "decision rather than a name search. The near-match is still written to the "
            "audit trail: 'we saw it and cleared it, here is why' is a different and better "
            "record than never having looked."
        ),
        "category": "goods",
        "form": {
            "legal_name": "Meridian Rail Components LLC",
            "country": "US",
            "entity_type": "LLC",
            "registration_number": "C9017722",
            "tax_id": "45-2210984",
            "address_line1": "980 Industrial Parkway",
            "address_city": "Pittsburgh, PA",
            "address_postcode": "15238",
            "address_country": "US",
            "contact_name": "Dmitri Volkov",
            "contact_email": "d.volkov@meridianrail.com",
            "website": "https://meridianrail.com",
            "business_description": "Machined steel components and couplings for freight rolling stock.",
            "directors": ["Dmitri Volkov"],
            # Same name as the OFAC_SDN entry; different person.
            # Listed:   Dmitri Volkov, DOB 1971-08-14, nationality RU
            # Supplied: Dmitri Volkov, DOB 1984-03-22, nationality US
            "director_details": [
                {"name": "Dmitri Volkov", "dob": "1984-03-22", "nationality": "US"},
            ],
        },
        "bank": {
            "account_name": "Meridian Rail Components LLC",
            "account_number": "440228176",
            "routing_number": "043000096",
            "bank_name": "First Keystone Bank",
            "bank_country": "US",
        },
        "custom_fields": {
            "nature_of_goods": "Machined steel couplings and drawgear for freight rolling stock.",
            "hsn_codes": "8607",
        },
        "documents": [
            {"doc_type": "tax_form", "filename": "w9_meridian.pdf",
             "extracted": {"kind": "w9", "legal_name": "Meridian Rail Components LLC",
                           "number": "45-2210984", "issue_date": _months_ago(5)}},
            {"doc_type": "bank_proof", "filename": "bank_letter_meridian.pdf",
             "extracted": {"kind": "bank_letter", "account_name": "Meridian Rail Components LLC",
                           "account_number": "440228176", "issue_date": _months_ago(1)}},
        ],
    },

    # The contrast case. Same machinery, opposite outcome — without this one
    # "we clear namesakes" is indistinguishable from "we never reject anyone".
    {
        "id": "sanctions-confirmed",
        "kind": "edge",
        "label": "Director confirmed on a sanctions list",
        "blurb": "Same name, and this time the date of birth and nationality match the listing too.",
        "expect": "REJECTED",
        "expect_why": ("Name AND both secondary identifiers agree, so screening raises "
                       "DENIED_PARTY_MATCH at REJECT — the one severity that terminates a case "
                       "without a human."),
        "teaches": (
            "The counterpart to the namesake, and the reason that case is not just leniency. "
            "This is one of the very few places an automated system should refuse outright "
            "rather than escalate: paying a sanctioned party is a criminal offence, and there "
            "is no commercial judgement for a reviewer to exercise. Note what the vendor is "
            "told — nothing specific. Disclosing a sanctions hit to its subject is tipping off."
        ),
        "category": "goods",
        "form": {
            "legal_name": "Volkov Maritime Trading Pte Ltd",
            "country": "SG",
            "entity_type": "PRIVATE_LIMITED",
            "registration_number": "201644821M",
            "tax_id": "M90884210K",
            "address_line1": "8 Shenton Way, #34-02",
            "address_city": "Singapore",
            "address_postcode": "068811",
            "address_country": "SG",
            "contact_name": "Dmitri Volkov",
            "contact_email": "ops@volkovmaritime.sg",
            "business_description": "Marine fuel brokerage and bulk cargo chartering.",
            "directors": ["Dmitri Volkov"],
            "director_details": [
                {"name": "Dmitri Volkov", "dob": "1971-08-14", "nationality": "RU"},
            ],
        },
        "bank": {
            "account_name": "Volkov Maritime Trading Pte Ltd",
            "account_number": "0123456789",
            "swift_bic": "DBSSSGSG",
            "bank_name": "DBS Bank",
            "bank_country": "SG",
        },
        "custom_fields": {
            "nature_of_goods": "Marine fuel and bulk cargo chartering.",
        },
        "documents": [],
    },

    # =====================================================================
    # EDGE CASE 3 — the requirement that must not be asked for
    # =====================================================================
    {
        "id": "sole-trader-no-incorporation",
        "kind": "edge",
        "label": "Individual professional with no company to incorporate",
        "blurb": ("A freelance architect. No certificate of incorporation exists, and no tax "
                  "registration either — she is below the threshold."),
        "expect": "APPROVED",
        "expect_why": ("The professional profile marks incorporation `na` and makes tax_form "
                       "conditional on `tax_id is present`. Neither is requested, so neither "
                       "is missing, so completeness passes."),
        "teaches": (
            "Generalisation is about what you STOP asking for. A one-size form demands a "
            "certificate of incorporation from an individual who cannot obtain one — the most "
            "common reason good freelancers abandon onboarding, and it produces a queue of "
            "PENDING_INFO cases that no reviewer can resolve either. The waiver lives in a "
            "JSON profile, not in an `if category == professional` branch, so adding a "
            "category ships no code. Watch the document list shrink when you open this: "
            "the government ID stands in as proof of identity, and the tax certificate "
            "disappears entirely because the tax ID field is empty."
        ),
        "category": "professional",
        "form": {
            "legal_name": "Ananya Krishnan",
            "country": "IN",
            "entity_type": "SOLE_TRADER",
            "registration_number": "",
            "tax_id": "",                       # below the registration threshold
            "pan": "AKRPK7788M",
            "address_line1": "22/3 Richmond Road",
            "address_city": "Bengaluru",
            "address_postcode": "560025",
            "address_country": "IN",
            "contact_name": "Ananya Krishnan",
            "contact_email": "ananya@ak-architecture.in",
            "business_description": "Independent architect providing residential design and site supervision.",
            "directors": ["Ananya Krishnan"],
        },
        "bank": {
            "account_name": "Ananya Krishnan",
            "account_number": "003701509876",
            "ifsc": "SBIN0007890",
            "bank_name": "State Bank of India",
            "bank_country": "IN",
        },
        "custom_fields": {
            "profession": "Architect",
            "engagement_basis": "Per project",
            "professional_body": "COA/2014/KA/8871",
        },
        "documents": [
            {"doc_type": "identity_proof", "filename": "passport_krishnan.pdf",
             "extracted": {"kind": "passport", "legal_name": "Ananya Krishnan",
                           "number": "Z4472819", "issue_date": _months_ago(40),
                           "expiry_date": _in_days(1600)}},
            {"doc_type": "pan_card", "filename": "pan_krishnan.pdf",
             "extracted": {"kind": "pan_card", "legal_name": "Ananya Krishnan",
                           "number": "AKRPK7788M", "issue_date": _months_ago(72)}},
            {"doc_type": "bank_proof", "filename": "cheque_krishnan.pdf",
             "extracted": {"kind": "cancelled_cheque", "account_name": "Ananya Krishnan",
                           "account_number": "003701509876", "issue_date": _months_ago(2)}},
            {"doc_type": "professional_certificate", "filename": "coa_krishnan.pdf",
             "extracted": {"kind": "licence", "legal_name": "Ananya Krishnan",
                           "number": "COA/2014/KA/8871", "issue_date": _months_ago(30),
                           "expiry_date": _in_days(900)}},
        ],
    },

    # =====================================================================
    # EDGE CASE 4 — neither pass nor fail
    # =====================================================================
    {
        "id": "licence-expiring-soon",
        "kind": "edge",
        "label": "Insurance valid today, expires in three weeks",
        "blurb": ("A construction contractor whose public liability cover is current but "
                  "lapses in 21 days."),
        "expect": "APPROVED_WITH_CONDITIONS",
        "expect_why": ("DOCUMENT_EXPIRING_SOON is raised at severity CONDITION, which sits "
                       "between ADVISORY and NEEDS_INFO and maps to its own status. The vendor "
                       "is onboarded and the renewal is recorded against them."),
        "teaches": (
            "A binary valid/expired test gets this wrong in both directions. Blocking a vendor "
            "whose cover is valid today is how teams learn to route around procurement when "
            "they have a deadline. Waving it through with no follow-up is how a contractor ends "
            "up on site next month with lapsed liability cover. The fourth verdict exists "
            "precisely for obligations that are satisfied now and will not be later. "
            "Note the one-way invariant: a condition can never upgrade a case to a clean "
            "APPROVED, only hold it at APPROVED_WITH_CONDITIONS or be overtaken by something "
            "worse."
        ),
        "category": "construction",
        "form": {
            "legal_name": "Girish Constructions Private Limited",
            "country": "IN",
            "entity_type": "PRIVATE_LIMITED",
            "registration_number": "U45200KA2012PTC066123",
            "tax_id": "29AAGCG5566H1Z4",
            "pan": "AAGCG5566H",
            "address_line1": "Plot 17, Peenya Industrial Area",
            "address_city": "Bengaluru",
            "address_postcode": "560058",
            "address_country": "IN",
            "contact_name": "Girish Prasad",
            "contact_email": "g.prasad@girishconstructions.co.in",
            "business_description": "Civil contracting for industrial warehousing and factory fit-out.",
            "directors": ["Girish Prasad"],
        },
        "bank": {
            "account_name": "Girish Constructions Private Limited",
            "account_number": "60110045678123",
            "ifsc": "KKBK0008123",
            "bank_name": "Kotak Mahindra Bank",
            "bank_country": "IN",
        },
        # workers_on_site > 5 pulls in safety_certification; > 0 pulls in
        # workers_insurance. contract_value is under the bond threshold, so no
        # performance bond is asked for — visible in the form as it renders.
        "custom_fields": {
            "trade_specialisation": "Civil works and industrial warehousing",
            "contract_value": 3200000,
            "workers_on_site": 85,
        },
        "documents": [
            {"doc_type": "tax_form", "filename": "gst_girish.pdf",
             "extracted": {"kind": "gst_certificate", "legal_name": "Girish Constructions Private Limited",
                           "number": "29AAGCG5566H1Z4", "issue_date": _months_ago(30)}},
            {"doc_type": "pan_card", "filename": "pan_girish.pdf",
             "extracted": {"kind": "pan_card", "legal_name": "Girish Constructions Private Limited",
                           "number": "AAGCG5566H", "issue_date": _months_ago(80)}},
            {"doc_type": "incorporation", "filename": "coi_girish.pdf",
             "extracted": {"kind": "certificate_of_incorporation",
                           "legal_name": "Girish Constructions Private Limited",
                           "number": "U45200KA2012PTC066123", "issue_date": "2012-08-14"}},
            {"doc_type": "bank_proof", "filename": "cheque_girish.pdf",
             "extracted": {"kind": "cancelled_cheque",
                           "account_name": "Girish Constructions Private Limited",
                           "account_number": "60110045678123", "issue_date": _months_ago(1)}},
            {"doc_type": "contractor_licence", "filename": "contractor_licence_girish.pdf",
             "extracted": {"kind": "licence", "legal_name": "Girish Constructions Private Limited",
                           "number": "CL-KA-20881", "issue_date": _months_ago(18),
                           "expiry_date": _in_days(600)}},
            # The point of the scenario: current today, lapsed in three weeks.
            {"doc_type": "workers_insurance", "filename": "workers_insurance_girish.pdf",
             "extracted": {"kind": "insurance_certificate",
                           "legal_name": "Girish Constructions Private Limited",
                           "number": "PLI-2291884", "issue_date": _months_ago(11),
                           "expiry_date": _in_days(21)}},
            {"doc_type": "safety_certification", "filename": "safety_girish.pdf",
             "extracted": {"kind": "licence", "legal_name": "Girish Constructions Private Limited",
                           "number": "OHS-KA-44120", "issue_date": _months_ago(6),
                           "expiry_date": _in_days(540)}},
        ],
    },
]

SCENARIOS_BY_ID = {s["id"]: s for s in SCENARIOS}


def list_scenarios() -> list[dict[str, Any]]:
    """Catalogue for the form's prefill menu — no document specs, they're bulky."""
    return [
        {k: s[k] for k in
         ("id", "kind", "label", "blurb", "expect", "expect_why", "teaches", "category")}
        for s in SCENARIOS
    ]


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    return SCENARIOS_BY_ID.get(scenario_id)


def to_submission_payload(scenario: dict[str, Any]) -> dict[str, Any]:
    """Flatten a scenario into the JSON body the submit endpoint accepts.

    One builder, used by the prefill endpoint, the tests and the demo seeder,
    so a scenario cannot pass its test while arriving at the form differently.

    The `extracted` blocks ride along on each document. That is the documented
    no-file path through the document reader (see SubmittedDocument): the
    cross-referencing, name matching and expiry logic run exactly as they do
    for an uploaded PDF, which is what lets a prefilled scenario demonstrate
    document verification without shipping binaries or asking the person
    demoing it to find four files.
    """
    return {
        **scenario["form"],
        "category": scenario["category"],
        "bank": scenario["bank"],
        "custom_fields": scenario["custom_fields"],
        "documents": [
            {"doc_type": d["doc_type"], "filename": d["filename"],
             "extracted": d.get("extracted", {})}
            for d in scenario["documents"]
        ],
    }
